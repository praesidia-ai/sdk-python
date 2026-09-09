"""
Praesidia SDK — AuditResource.

Covers the ``/organizations/{org_id}/audit-logs`` endpoints.
"""

from __future__ import annotations

from typing import Any, Iterator

from ._http import HttpClient
from ._evidence import evidence_date_range


class AuditResource:
    """
    Access the organisation audit log.

    Endpoint base: ``/organizations/{org_id}/audit-logs``
    """

    def __init__(self, http: HttpClient) -> None:
        self._http = http
        self._base = f"/organizations/{http.org_id}/audit-logs"
        self._bundle_path = f"/organizations/{http.org_id}/audit/bundle"

    def export_bundle(self, *, from_date: str, to_date: str) -> bytes:
        """Download a signed ZIP for offline verification (at most 90 days).

        Requires audit:read and owner/compliance-officer access with COMPLIANCE_VIEW.
        This is separate from the JSON/CSV log export. The bounded transport caps
        downloads at 128 MiB. A successful download does not verify the evidence.
        """
        evidence_date_range(from_date, to_date, bundle=True)
        response = self._http.stream_get(self._bundle_path, params={"from": from_date, "to": to_date})
        self._http._raise_for_status(response)
        return response.content

    def list(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int = 50,
        page: int = 1,
        action: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return a page of audit log entries.

        Args:
            from_date: ISO 8601 start date/time (e.g. ``"2026-01-01"``).
            to_date:   ISO 8601 end date/time.
            limit:     Maximum number of entries to return (default: 50).
            page:      1-based page number (default: 1).
            action:    Filter by action type (e.g. ``"AGENT_CREATED"``).

        Returns:
            A list of audit event dicts.

        Note:
            BUGHUNT-SDK-03 — there is deliberately NO ``resource_type``
            filter. The backend ``FilterAuditDto`` whitelists only
            ``search`` / ``action`` / ``startDate`` / ``endDate`` and runs
            under ``forbidNonWhitelisted``, so sending ``resourceType``
            made the WHOLE request 400. ``resourceType`` is a value the
            backend *derives* from the ``action`` prefix at read time; it
            is not a stored, queryable column. Filter by ``action`` (e.g.
            ``action="agent.created"``) instead.
        """
        params: dict[str, Any] = {"page": page, "limit": limit}
        if from_date is not None:
            params["startDate"] = from_date
        if to_date is not None:
            params["endDate"] = to_date
        if action is not None:
            params["action"] = action

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
        advancing until the server returns an EMPTY page.

        BUGHUNT-SDK-01 — the terminal condition is an empty page, NOT a
        short one. The backend hard-caps the page size
        (``PAGINATION_MAX_LIMIT = 100`` via ``clampLimit``), so a caller
        asking for ``limit > 100`` still receives at most 100 rows per
        page. The old ``len(events) < limit`` heuristic therefore treated
        the very first (full-but-clamped) page as the last one and
        silently dropped every event past the first 100 — the worst
        possible failure for a compliance/audit export. Stopping only on
        an empty page needs no knowledge of the server's cap and streams
        the full range.

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
                result
                if isinstance(result, list)
                else result.get("data", result.get("logs", []))
            )

            # An empty page is the only reliable end-of-stream signal: a
            # non-empty page shorter than `limit` may just be the server's
            # clamp (100), not the end of the data.
            if not events:
                break

            for event in events:
                yield event

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
        if format not in ("json", "csv"):
            raise ValueError("format must be 'json' or 'csv'")
        params: dict[str, Any] = {"format": format}
        if from_date is not None:
            params["startDate"] = from_date
        if to_date is not None:
            params["endDate"] = to_date

        r = self._http.stream_get(f"{self._base}/export", params=params)
        self._http._raise_for_status(r)
        return r.content
