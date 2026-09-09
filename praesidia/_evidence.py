"""Shared, timezone-explicit date windows for evidence reads and exports."""
from __future__ import annotations

from datetime import datetime, timezone
import re

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}(?:T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,3})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d))?$")


def evidence_date_range(from_date: str | None, to_date: str | None, *, bundle: bool = False) -> None:
    values = []
    for name, value in (("from", from_date), ("to", to_date)):
        if value is None:
            if bundle:
                raise ValueError("from and to are required for a signed bundle")
            values.append(None)
            continue
        if not isinstance(value, str) or not _ISO.fullmatch(value):
            raise ValueError(f"{name} must be a UTC date or an ISO timestamp with a timezone")
        try:
            date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"{name} must be a valid ISO-8601 date") from error
        values.append(date.replace(tzinfo=timezone.utc) if date.tzinfo is None else date)
    if values[0] is not None and values[1] is not None:
        seconds = (values[1] - values[0]).total_seconds()
        if seconds < 0:
            raise ValueError("from must be earlier than or equal to to")
        if bundle and (seconds <= 0 or seconds > 90 * 24 * 60 * 60):
            raise ValueError("signed bundle range must be greater than zero and at most 90 days")
