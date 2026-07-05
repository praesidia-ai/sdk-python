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
        api_key:  API key used in the ``X-API-Key`` request header.
        org_id:   Organisation UUID scoped into every resource path.
        base_url: Base URL of the Praesidia backend.
    """

    def __init__(self, api_key: str, org_id: str, base_url: str) -> None:
        self.org_id = org_id
        self._base = base_url.rstrip("/")
        self._headers: dict[str, str] = {
            "X-API-Key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def set_api_key(self, api_key: str) -> None:
        """
        Swap the credential this client authenticates with, at runtime.

        Enables zero-downtime credential rotation for a long-lived client:
        after rotating an agent's client secret (see
        :meth:`~praesidia.agents.AgentsResource.rotate_client_secret`) with a
        grace window, adopt the new secret here and rely on the server-side
        grace overlap so in-flight callers are never rejected during the swap.

        Security: the new credential is held only in memory and is never logged.
        """
        self._headers["X-API-Key"] = api_key

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

    def stream_get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """
        Return a streaming ``httpx.Response`` for chunked GET requests.

        The caller is responsible for iterating the response and closing it.
        """
        url = f"{self._base}{path}"
        return httpx.get(
            url,
            headers=self._headers,
            params=params,
            timeout=None,  # streaming — no timeout
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
