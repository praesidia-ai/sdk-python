"""
Praesidia SDK — thin HTTP transport layer.

Wraps httpx for synchronous requests.  Async support can be added in a
future release via httpx.AsyncClient without changing the resource API.
"""

from __future__ import annotations

from threading import RLock
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from .exceptions import (
    AuthError,
    ForbiddenError,
    NotFoundError,
    PraesidiaError,
    RateLimitError,
    ServerError,
)

_DEFAULT_TIMEOUT = 30.0  # seconds
_MAX_TIMEOUT = 300.0  # seconds

#: BUGHUNT-SDK-06 — timeout budget for bulk download/export calls
#: (``stream_get``: report PDF, audit export, analytics export). httpx
#: timeouts are PER-OPERATION (idle), NOT a total wall-clock cap, so a
#: generous ``read`` idle timeout lets a large-but-progressing export
#: finish while a stalled peer still fails fast instead of hanging the
#: client forever (the old ``timeout=None`` disabled connect/read/write/
#: pool timeouts entirely — a permanent hang on any mid-stream stall).
_DOWNLOAD_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)

#: Q3-02 — canonical chain-trace propagation header.
CHAIN_ID_HEADER = "X-Praesidia-Chain-Id"
#: Q4-02 — task-binding headers forwarded on a task-scoped MCP tool call.
TASK_ID_HEADER = "X-Praesidia-Task-Id"
AGENT_ID_HEADER = "X-Praesidia-Agent-Id"
CAPABILITY_TOKEN_HEADER = "X-Praesidia-Capability-Token"


def path_segment(value: str, name: str = "path segment") -> str:
    """Validate and percent-encode a caller-controlled URL path segment."""
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or value in (".", "..")
    ):
        raise ValueError(
            f"{name} must be a non-empty path segment without surrounding "
            "whitespace or dot traversal"
        )
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{name} must not contain control characters")
    return quote(value, safe="")


def normalize_base_url(base_url: str) -> str:
    """Return a safe absolute HTTP(S) API base URL without a trailing slash."""
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("base_url must be a non-empty absolute HTTP(S) URL")
    if base_url != base_url.strip() or any(char.isspace() for char in base_url):
        raise ValueError("base_url must not contain whitespace or control characters")
    if "\\" in base_url:
        raise ValueError("base_url must not contain backslashes")
    parsed = urlsplit(base_url)
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("base_url contains an invalid port") from exc
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "base_url must be an absolute HTTP(S) URL without credentials, "
            "a query, or a fragment"
        )
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _validate_api_key(api_key: str) -> str:
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError("api_key must be a non-empty string")
    if api_key != api_key.strip() or any(ord(char) < 32 or ord(char) == 127 for char in api_key):
        raise ValueError("api_key must not contain surrounding whitespace or control characters")
    return api_key


def _validate_timeout(timeout: float) -> float:
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ValueError("timeout must be a number of seconds")
    value = float(timeout)
    if not 0 < value <= _MAX_TIMEOUT:
        raise ValueError(f"timeout must be greater than 0 and at most {_MAX_TIMEOUT}s")
    return value


class HttpClient:
    """
    Minimal HTTP client for the Praesidia management API.

    Args:
        api_key:  API key sent in the ``Authorization: Bearer <key>`` header.
        org_id:   Organisation UUID scoped into every resource path.
        base_url: Base URL of the Praesidia backend.

    AUDIT-SDK-04 — the SDK authenticates with ``Authorization: Bearer <key>``
    (matching the TS SDK, the CLI, and the backend's canonical ``ApiKeyStrategy``
    / ``OrAuthGuard``, which read the credential ONLY from ``Authorization:
    Bearer``). The prior ``X-API-Key`` header authenticated only on the custom
    ``JwtOrApiKeyGuard`` routes and 401'd on every ``OrAuthGuard`` /
    passport-``api-key`` route (e.g. guardrails), so Bearer makes Python work
    everywhere the TS SDK does.
    """

    def __init__(
        self,
        api_key: str,
        org_id: str,
        base_url: str,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.org_id = path_segment(org_id, "org_id")
        self._base = normalize_base_url(base_url)
        self._timeout = _validate_timeout(timeout)
        self._headers_lock = RLock()
        self._headers: dict[str, str] = {
            "Authorization": f"Bearer {_validate_api_key(api_key)}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def set_api_key(self, api_key: str) -> None:
        """
        Swap the credential this client authenticates with, at runtime.

        Enables zero-downtime credential rotation for a long-lived client:
        adopt a newly provisioned agent client secret here so subsequent
        requests authenticate with the new secret without recreating the
        client.

        Security: the new credential is held only in memory and is never logged.
        """
        with self._headers_lock:
            self._headers["Authorization"] = f"Bearer {_validate_api_key(api_key)}"

    def set_chain_id(self, chain_id: str | None) -> None:
        """
        Q3-02 — adopt the inbound chain-trace id so it is forwarded (unchanged)
        on every subsequent outbound call as the ``X-Praesidia-Chain-Id``
        header. Pass ``None`` (or an empty value) to stop propagating.

        The SDK NEVER mints a chainId — it only echoes one received on an
        inbound hop so a multi-agent chain stays correlated across SDK-driven
        hops. Chain ids are unsigned metadata.
        """
        if chain_id is not None and not isinstance(chain_id, str):
            raise ValueError("chain_id must be a string or None")
        if chain_id and ("\r" in chain_id or "\n" in chain_id):
            raise ValueError("chain_id must not contain newline characters")
        with self._headers_lock:
            if chain_id:
                self._headers[CHAIN_ID_HEADER] = chain_id
            else:
                self._headers.pop(CHAIN_ID_HEADER, None)

    def get_chain_id(self) -> str | None:
        """Return the chain-trace id currently being propagated, if any."""
        with self._headers_lock:
            return self._headers.get(CHAIN_ID_HEADER)

    def _merged_headers(
        self,
        extra: dict[str, str] | None,
        *,
        include_auth: bool = True,
    ) -> dict[str, str]:
        """Base headers (auth + chain) plus optional per-request extras."""
        with self._headers_lock:
            base = dict(self._headers)
        if not include_auth:
            base.pop("Authorization", None)
        if not extra:
            return base
        return {**base, **extra}

    # ------------------------------------------------------------------
    # Public verbs
    # ------------------------------------------------------------------

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        include_auth: bool = True,
    ) -> Any:
        """Send a GET request and return the parsed JSON body."""
        url = f"{self._base}{path}"
        r = httpx.get(
            url,
            headers=self._merged_headers(headers, include_auth=include_auth),
            params=params,
            timeout=self._timeout,
        )
        self._raise_for_status(r)
        return r.json()

    def post(
        self,
        path: str,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Send a POST request and return the parsed JSON body."""
        url = f"{self._base}{path}"
        r = httpx.post(
            url,
            headers=self._merged_headers(headers),
            json=json,
            timeout=self._timeout,
        )
        self._raise_for_status(r)
        return r.json()

    def patch(self, path: str, json: dict[str, Any] | None = None) -> Any:
        """Send a PATCH request and return the parsed JSON body."""
        url = f"{self._base}{path}"
        r = httpx.patch(
            url,
            headers=self._merged_headers(None),
            json=json,
            timeout=self._timeout,
        )
        self._raise_for_status(r)
        return r.json()

    def delete(self, path: str) -> None:
        """Send a DELETE request (no response body expected)."""
        url = f"{self._base}{path}"
        r = httpx.delete(
            url, headers=self._merged_headers(None), timeout=self._timeout
        )
        self._raise_for_status(r)

    def stream_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        timeout: httpx.Timeout | float | None = None,
    ) -> httpx.Response:
        """
        Buffered GET for bulk download/export endpoints (report PDF, audit
        export, analytics export).

        BUGHUNT-SDK-06 — uses a generous but finite per-operation timeout
        (``_DOWNLOAD_TIMEOUT``) rather than ``timeout=None``. httpx's
        ``read`` timeout is the idle-between-chunks budget, NOT a total
        cap, so it does not truncate a large-but-progressing export while
        still failing fast on a stalled peer. Pass ``timeout=`` to override
        for an unusually long or short transfer. (Despite the name this
        reads the whole body into ``.content``; a future true-streaming
        variant can switch to ``httpx.stream()``.)
        """
        url = f"{self._base}{path}"
        return httpx.get(
            url,
            headers=self._merged_headers(None),
            params=params,
            timeout=_DOWNLOAD_TIMEOUT if timeout is None else timeout,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _raise_for_status(self, r: httpx.Response) -> None:
        """Map HTTP error codes to typed SDK exceptions."""
        if r.status_code == 401:
            raise AuthError(r.text)
        if r.status_code == 403:
            raise ForbiddenError(r.text)
        if r.status_code == 404:
            raise NotFoundError(r.text)
        if r.status_code == 429:
            raise RateLimitError(r.text)
        if r.status_code >= 500:
            raise ServerError(r.text, r.status_code)
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PraesidiaError(str(exc), status_code=r.status_code) from exc
