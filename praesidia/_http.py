"""
Praesidia SDK — thin HTTP transport layer.

Wraps httpx for synchronous requests.  Async support can be added in a
future release via httpx.AsyncClient without changing the resource API.
"""

from __future__ import annotations

from typing import Any

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

    def __init__(self, api_key: str, org_id: str, base_url: str) -> None:
        self.org_id = org_id
        self._base = base_url.rstrip("/")
        self._headers: dict[str, str] = {
            "Authorization": f"Bearer {api_key}",
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
        self._headers["Authorization"] = f"Bearer {api_key}"

    def set_chain_id(self, chain_id: str | None) -> None:
        """
        Q3-02 — adopt the inbound chain-trace id so it is forwarded (unchanged)
        on every subsequent outbound call as the ``X-Praesidia-Chain-Id``
        header. Pass ``None`` (or an empty value) to stop propagating.

        The SDK NEVER mints a chainId — it only echoes one received on an
        inbound hop so a multi-agent chain stays correlated across SDK-driven
        hops. Chain ids are unsigned metadata.
        """
        if chain_id:
            self._headers[CHAIN_ID_HEADER] = chain_id
        else:
            self._headers.pop(CHAIN_ID_HEADER, None)

    def get_chain_id(self) -> str | None:
        """Return the chain-trace id currently being propagated, if any."""
        return self._headers.get(CHAIN_ID_HEADER)

    def _merged_headers(
        self, extra: dict[str, str] | None
    ) -> dict[str, str]:
        """Base headers (auth + chain) plus optional per-request extras."""
        if not extra:
            return self._headers
        return {**self._headers, **extra}

    # ------------------------------------------------------------------
    # Public verbs
    # ------------------------------------------------------------------

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Send a GET request and return the parsed JSON body."""
        url = f"{self._base}{path}"
        r = httpx.get(
            url,
            headers=self._merged_headers(headers),
            params=params,
            timeout=_DEFAULT_TIMEOUT,
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
            timeout=_DEFAULT_TIMEOUT,
        )
        self._raise_for_status(r)
        return r.json()

    def patch(self, path: str, json: dict[str, Any] | None = None) -> Any:
        """Send a PATCH request and return the parsed JSON body."""
        url = f"{self._base}{path}"
        r = httpx.patch(url, headers=self._headers, json=json, timeout=_DEFAULT_TIMEOUT)
        self._raise_for_status(r)
        return r.json()

    def delete(self, path: str) -> None:
        """Send a DELETE request (no response body expected)."""
        url = f"{self._base}{path}"
        r = httpx.delete(url, headers=self._headers, timeout=_DEFAULT_TIMEOUT)
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
            headers=self._headers,
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
