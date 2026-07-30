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

import random
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
        max_elapsed_s: Wall-clock budget in seconds across every attempt of
                       one logical call (default 15.0).
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
        or value.base_delay_s < 0
    ):
        raise ValueError("retry.base_delay_s must be a non-negative number")
    if (
        isinstance(value.max_delay_s, bool)
        or not isinstance(value.max_delay_s, (int, float))
        or value.max_delay_s < value.base_delay_s
    ):
        raise ValueError("retry.max_delay_s must be a number >= retry.base_delay_s")
    if (
        isinstance(value.max_elapsed_s, bool)
        or not isinstance(value.max_elapsed_s, (int, float))
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
        if seconds >= 0:
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
