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
