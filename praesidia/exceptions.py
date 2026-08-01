"""
Praesidia SDK — exception hierarchy.

All SDK-level errors derive from PraesidiaError so callers can catch the
whole family with a single except clause if needed.
"""

from __future__ import annotations


class PraesidiaError(Exception):
    """Base class for all Praesidia SDK errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message

    def __repr__(self) -> str:  # pragma: no cover
        return f"{self.__class__.__name__}(status_code={self.status_code!r}, message={self.message!r})"


class AuthError(PraesidiaError):
    """Raised when the API returns HTTP 401 (missing or invalid credentials)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=401)


class ForbiddenError(PraesidiaError):
    """Raised when the API returns HTTP 403 (insufficient permissions)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=403)


class NotFoundError(PraesidiaError):
    """Raised when the API returns HTTP 404."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=404)


class RateLimitError(PraesidiaError):
    """Raised when the API returns HTTP 429 (rate limit exceeded)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=429)


class ServerError(PraesidiaError):
    """Raised when the API returns an unexpected 5xx response."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message, status_code=status_code)


class ProtectedActionDeniedError(PraesidiaError):
    """
    PA01 DX-002 — raised by ``AgentsResource.protect_action`` when the
    managed MCP Proof Edge (or an upstream policy/RBAC gate on the same
    route) denies the dispatch: no/expired/invalid/replayed Permit, a
    commitment mismatch, or a confirmed replay (``DUPLICATE_SUPPRESSED``,
    which denies even in observe-mode — see D8/D9). Distinct from a
    tool-level failure (the tool dispatched and reported its OWN error —
    that does not raise; it comes back in the returned dict's ``isError``).

    ``action_id``/``closure`` are populated once ``be``'s response carries
    them (see ``.claude/tickets/PA01-CONTRACT-sdk-action-response.md``) —
    ``None`` until that contract lands.
    """

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        action_id: str | None = None,
        closure: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.action_id = action_id
        self.closure = closure


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
