"""
Praesidia SDK — AuditResource.

Covers the ``/organizations/{org_id}/audit-logs`` endpoints.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

from ._http import HttpClient


class AuditResource:
    """
    Access the organisation audit log.

    Endpoint base: ``/organizations/{org_id}/audit-logs``
    """

    def __init__(self, http: HttpClient) -> None:
        self._http = http
        self._base = f"/organizations/{http.org_id}/audit-logs"

    def list(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int = 50,
        page: int = 1,
        action: str | None = None,
        resource_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return a page of audit log entries.

        Args:
            from_date:     ISO 8601 start date/time (e.g. ``"2026-01-01"``).
            to_date:       ISO 8601 end date/time.
            limit:         Maximum number of entries to return (default: 50).
            page:          1-based page number (default: 1).
            action:        Filter by action type (e.g. ``"AGENT_CREATED"``).
            resource_type: Filter by resource type (e.g. ``"agent"``).

        Returns:
            A list of audit event dicts.
        """
        params: dict[str, Any] = {"page": page, "limit": limit}
        if from_date is not None:
            params["startDate"] = from_date
        if to_date is not None:
            params["endDate"] = to_date
        if action is not None:
            params["action"] = action
        if resource_type is not None:
            params["resourceType"] = resource_type

        result = self._http.get(self._base, params=params)
        if isinstance(result, list):
            return result
        return result.get("data", result.get("logs", []))

    def stream(
        self,
        from_date: str | None = None,
        limit: int = 100,
    ) -> Iterator[dict[str, Any]]:
        """
        Yield audit log events as a lazy iterator.

        Internally pages through ``/audit-logs`` using ``limit`` per call,
        stopping when a page returns fewer results than ``limit`` (last page).

        Args:
            from_date: ISO 8601 start date/time.  When ``None`` the API
                       default applies (typically most-recent first).
            limit:     Page size used for each internal fetch (default: 100).

        Yields:
            Individual audit event dicts.
        """
        page = 1
        while True:
            params: dict[str, Any] = {"page": page, "limit": limit}
            if from_date is not None:
                params["startDate"] = from_date

            result = self._http.get(self._base, params=params)
            events: list[dict[str, Any]] = (
                result if isinstance(result, list) else result.get("data", result.get("logs", []))
            )

            for event in events:
                yield event

            # Stop if this was the last page.
            if len(events) < limit:
                break
            page += 1

    def export(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        format: str = "json",
    ) -> bytes:
        """
        Export the audit log in bulk.

        Calls ``/organizations/{org_id}/audit-logs/export``.

        Args:
            from_date: ISO 8601 start date/time.
            to_date:   ISO 8601 end date/time.
            format:    Export format — ``"json"`` or ``"csv"`` (default: ``"json"``).

        Returns:
            Raw export bytes.
        """
        params: dict[str, Any] = {"format": format}
        if from_date is not None:
            params["startDate"] = from_date
        if to_date is not None:
            params["endDate"] = to_date

        r = self._http.stream_get(f"{self._base}/export", params=params)
        self._http._raise_for_status(r)
        return r.content
