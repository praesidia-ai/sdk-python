"""
Praesidia SDK -- bounded retry policy (FINDING-4; parity with the TS SDK's
``retry.ts``).

Retries are applied ONLY to requests known to be safe to repeat: GET/DELETE
(always idempotent in this API) and any POST/PATCH the caller explicitly
marks with ``idempotency_key``. A bare POST (task submission, agent/workflow/
connection creation) is NEVER retried by :class:`~praesidia._http.HttpClient`
-- retrying an already-applied write after a transient timeout would risk a
duplicate create/charge.
"""

from __future__ import annotations

import math
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional, Union


@dataclass(frozen=True)
class RetryConfig:
    """
    Args:
        max_attempts: Total attempts including the first (default 3 -- i.e.
                      up to 2 retries). Must be 1..10.
        base_delay_s: Base delay in seconds before the first retry (default 0.25).
        max_delay_s:  Cap on any single computed backoff delay, before a
                      ``Retry-After`` override (default 4.0).
        max_elapsed_s: Monotonic elapsed-time budget in seconds across one
                       logical call (default 15.0).
    """

    max_attempts: int = 3
    base_delay_s: float = 0.25
    max_delay_s: float = 4.0
    max_elapsed_s: float = 15.0


DEFAULT_RETRY_CONFIG = RetryConfig()


def resolve_retry_config(
    value: Union[RetryConfig, bool, None],
) -> Optional[RetryConfig]:
    """
    Validate a caller-supplied retry config. ``False`` disables retries
    entirely (returns ``None``); ``None`` resolves to the default policy.
    """
    if value is False:
        return None
    if value is None:
        value = DEFAULT_RETRY_CONFIG
    if not isinstance(value, RetryConfig):
        raise ValueError("retry must be a RetryConfig instance, False, or None")
    if (
        isinstance(value.max_attempts, bool)
        or not isinstance(value.max_attempts, int)
        or not 1 <= value.max_attempts <= 10
    ):
        raise ValueError("retry.max_attempts must be an integer from 1 to 10")
    if (
        isinstance(value.base_delay_s, bool)
        or not isinstance(value.base_delay_s, (int, float))
        or not math.isfinite(value.base_delay_s)
        or value.base_delay_s < 0
    ):
        raise ValueError("retry.base_delay_s must be a non-negative number")
    if (
        isinstance(value.max_delay_s, bool)
        or not isinstance(value.max_delay_s, (int, float))
        or not math.isfinite(value.max_delay_s)
        or value.max_delay_s < value.base_delay_s
    ):
        raise ValueError("retry.max_delay_s must be a number >= retry.base_delay_s")
    if (
        isinstance(value.max_elapsed_s, bool)
        or not isinstance(value.max_elapsed_s, (int, float))
        or not math.isfinite(value.max_elapsed_s)
        or value.max_elapsed_s < 0
    ):
        raise ValueError("retry.max_elapsed_s must be a non-negative number")
    return value


def is_retryable_status(status_code: int) -> bool:
    """Retry-worthy HTTP status codes: 429 (honour Retry-After) and 5xx."""
    return status_code == 429 or 500 <= status_code <= 599


def parse_retry_after_s(header_value: Optional[str]) -> Optional[float]:
    """
    Parse a ``Retry-After`` header (delta-seconds or an HTTP-date) into a
    delay in seconds. Returns ``None`` when absent or unparseable.
    """
    if not header_value:
        return None
    try:
        seconds = float(header_value)
        if math.isfinite(seconds) and seconds >= 0:
            return seconds
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(header_value)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = (dt - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, delta)


def compute_backoff_s(attempt: int, base_delay_s: float, max_delay_s: float) -> float:
    """Exponential backoff with full jitter, capped at ``max_delay_s``."""
    exp = min(max_delay_s, base_delay_s * (2 ** (attempt - 1)))
    return random.random() * exp


#: R-SDK-1 — be-core honours ``Idempotency-Key`` for safe replay on exactly
#: two route families (grepped ``be/src`` for consumers of the header):
#: ``POST /organizations/:orgId/tasks`` (``agent-tasks.controller.ts``, via
#: ``withIdempotency``) and the A2A inbound routes (``POST /a2a/tasks``,
#: ``POST /a2a/tasks/:taskId/result``). Every other POST/PATCH in the API --
#: including every PATCH route today -- ignores the header entirely. Parity
#: with the TS SDK's ``retry.ts``.
_IDEMPOTENCY_HONOURED_POST_PATHS = (
    re.compile(r"^/organizations/[^/]+/tasks$"),
    re.compile(r"^/a2a/tasks$"),
    re.compile(r"^/a2a/tasks/[^/]+/result$"),
)


def assert_idempotency_key_supported(method: str, path: str) -> None:
    """
    Raise ``ValueError`` when ``idempotency_key`` is requested for a
    ``method``+``path`` combination that be-core does not deduplicate
    server-side. Called only when the caller actually supplied an
    ``idempotency_key`` -- a bare request never invokes this and is never
    retried.
    """
    if method == "POST" and any(
        pattern.match(path) for pattern in _IDEMPOTENCY_HONOURED_POST_PATHS
    ):
        return
    raise ValueError(
        f"be-core does not honour Idempotency-Key on {method} {path} -- "
        "retry-on-write is only safe for routes with server-side dedup "
        "(today: POST /organizations/:orgId/tasks, POST /a2a/tasks, "
        "POST /a2a/tasks/:taskId/result). Passing idempotency_key here would "
        "let a transient 5xx double-apply a write the server does not "
        "deduplicate."
    )
