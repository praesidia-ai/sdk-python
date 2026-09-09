"""
Praesidia SDK — exception hierarchy.

All SDK-level errors derive from PraesidiaError so callers can catch the
whole family with a single except clause if needed.
"""

from __future__ import annotations

from typing import Any


class PraesidiaError(Exception):
    """Base class for all Praesidia SDK errors.

    SCAN2-007 -- ``message``/``status_code`` keep their original meaning and
    format for backwards compatibility: any caller that already reads them
    keeps working unchanged. ``code``/``request_id``/``details``/
    ``retry_after``/``retryable`` are new, purely additive, keyword-only
    attributes read from be's structured error envelope
    (``be/src/common/filters/http-exception.filter.ts``) when the response
    body parses as a JSON object; each is ``None``/``False`` when the body
    doesn't carry that field (or isn't JSON at all -- a caller must not
    assume they are populated). ``body`` is the full raw parsed envelope (or
    ``None`` if the response wasn't valid JSON), so a field be adds later is
    never silently dropped even by an SDK version that predates it.
    """

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        *,
        code: str | None = None,
        request_id: str | None = None,
        details: Any = None,
        retry_after: float | None = None,
        retryable: bool = False,
        body: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.request_id = request_id
        self.details = details
        self.retry_after = retry_after
        self.retryable = retryable
        self.body = body

    def __repr__(self) -> str:  # pragma: no cover
        return f"{self.__class__.__name__}(status_code={self.status_code!r}, message={self.message!r})"


class AuthError(PraesidiaError):
    """Raised when the API returns HTTP 401 (missing or invalid credentials)."""

    def __init__(self, message: str, **envelope_kwargs: Any) -> None:
        super().__init__(message, status_code=401, **envelope_kwargs)


class ForbiddenError(PraesidiaError):
    """Raised when the API returns HTTP 403 (insufficient permissions)."""

    def __init__(self, message: str, **envelope_kwargs: Any) -> None:
        super().__init__(message, status_code=403, **envelope_kwargs)


class NotFoundError(PraesidiaError):
    """Raised when the API returns HTTP 404."""

    def __init__(self, message: str, **envelope_kwargs: Any) -> None:
        super().__init__(message, status_code=404, **envelope_kwargs)


class RateLimitError(PraesidiaError):
    """Raised when the API returns HTTP 429 (rate limit exceeded)."""

    def __init__(self, message: str, **envelope_kwargs: Any) -> None:
        super().__init__(message, status_code=429, **envelope_kwargs)


class ServerError(PraesidiaError):
    """Raised when the API returns an unexpected 5xx response."""

    def __init__(self, message: str, status_code: int, **envelope_kwargs: Any) -> None:
        super().__init__(message, status_code=status_code, **envelope_kwargs)


class ResponseTooLargeError(PraesidiaError):
    """Raised before an upstream response can exceed the SDK's memory cap."""

    def __init__(self, path: str, limit_bytes: int, status_code: int) -> None:
        super().__init__(
            f"Praesidia response body for {path} exceeds the "
            f"{limit_bytes}-byte limit",
            status_code=status_code,
        )
        self.path = path
        self.limit_bytes = limit_bytes


class ProtectedActionDeniedError(PraesidiaError):
    """
    PA01 DX-002 — raised by ``AgentsResource.protect_action`` when the
    managed MCP Proof Edge (or an upstream policy/RBAC gate on the same
    route) denies the dispatch: no/expired/invalid/replayed Permit, a
    commitment mismatch, or a confirmed replay (``DUPLICATE_SUPPRESSED``,
    which denies even in observe-mode — see D8/D9). Distinct from a
    tool-level failure (the tool dispatched and reported its OWN error —
    that does not raise; it comes back in the returned dict's ``isError``).

    PA-0026 — ``protect_action`` raises this if and only if ``be``'s
    response carries ``actionDenyReason`` (mirrored below as
    ``action_deny_reason``, one of ``"PERMIT_MISSING"`` | ``"PERMIT_INVALID"``
    | ``"PERMIT_EXPIRED"`` | ``"PERMIT_MISMATCH"`` | ``"PERMIT_REPLAYED"`` |
    ``"POLICY_DENIED"``); a downstream tool/transport error never reaches
    this constructor. ``action_id``/``closure`` are populated whenever
    ``be``'s response carries them — ``None`` when the denial happened
    before the Proof Edge block ran (``action_deny_reason ==
    "POLICY_DENIED"``).
    """

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        action_id: str | None = None,
        closure: str | None = None,
        action_deny_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.action_id = action_id
        self.closure = closure
        self.action_deny_reason = action_deny_reason


class UnsupportedProtectedActionTargetError(PraesidiaError):
    """
    PA01 DX-002 — raised by ``AgentsResource.protect_action`` when the
    target's ``protocol`` is not the managed MCP path. Fails LOUDLY rather
    than silently downgrading to ``call_mcp_tool``-style best-effort (no
    throw on deny) behaviour — the customer-controlled Proof Edge that would
    make an arbitrary destination honestly protectable (EDGE-003) is
    explicitly out of PA01 scope (D11).
    """

    def __init__(self, protocol: str) -> None:
        super().__init__(
            f'protect_action: target protocol "{protocol}" is unsupported until '
            "the customer-controlled Proof Edge ships (EDGE-003 — out of scope "
            'for the current release). Only the managed MCP path ("mcp") is '
            "supported by this SDK version."
        )
        self.protocol = protocol
