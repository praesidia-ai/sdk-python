"""
Praesidia SDK — AuditResource.

Covers the ``/organizations/{org_id}/audit-logs`` endpoints.
"""

from __future__ import annotations

import re
from typing import Any, Iterator

from ._http import HttpClient, path_segment
from ._evidence import evidence_date_range

# be validates package / AI System ids with ParseUUIDPipe / @IsUUID (any version).
_UUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")


def _uuid(value: str, name: str) -> str:
    if not isinstance(value, str) or not _UUID.fullmatch(value):
        raise ValueError(f"{name} must be a UUID")
    return value


class AuditBundle(bytes):
    """The signed bundle ZIP bytes plus the window the server actually cut (BE-1629).

    ``effective_to`` is earlier than ``requested_to`` when the range end was clamped to
    the last Merkle-rooted hour; ``window_clamp`` says why (``none``,
    ``clamped_to_last_rooted_hour``, ``no_rooted_hour`` or ``include_unrooted``).
    Each is ``None`` when the server did not send the header.
    """

    requested_to: str | None
    effective_to: str | None
    window_clamp: str | None


class AuditResource:
    """
    Access the organisation audit log.

    Endpoint base: ``/organizations/{org_id}/audit-logs``
    """

    def __init__(self, http: HttpClient) -> None:
        self._http = http
        self._base = f"/organizations/{http.org_id}/audit-logs"
        self._audit = f"/organizations/{http.org_id}/audit"
        self._bundle_path = f"{self._audit}/bundle"

    def export_bundle(self, *, from_date: str, to_date: str, include_unrooted: bool = False) -> AuditBundle:
        """Download a signed ZIP for offline verification (at most 90 days).

        Requires audit:read and owner/compliance-officer access with COMPLIANCE_VIEW.
        This is separate from the JSON/CSV log export. The bounded transport caps
        downloads at 128 MiB. A successful download does not verify the evidence:
        run ``praesidia-verify`` on it. The server cuts the range at the last
        Merkle-rooted hour unless ``include_unrooted=True``; read
        ``effective_to`` / ``window_clamp`` on the result to see the cut.
        """
        evidence_date_range(from_date, to_date, bundle=True)
        params: dict[str, Any] = {"from": from_date, "to": to_date}
        if include_unrooted:
            params["includeUnrooted"] = "true"
        response = self._http.stream_get(self._bundle_path, params=params)
        self._http._raise_for_status(response)
        bundle = AuditBundle(response.content)
        bundle.requested_to = response.headers.get("X-Praesidia-Requested-To")
        bundle.effective_to = response.headers.get("X-Praesidia-Effective-To")
        bundle.window_clamp = response.headers.get("X-Praesidia-Window-Clamp")
        return bundle

    def get_decision_receipt(self, decision_id: str) -> dict[str, Any]:
        """Fetch the Decision Receipt for a ``decisionId``. GET .../audit/decisions/:decisionId/receipt.

        Raises ``NotFoundError`` when no receipt has this id in the caller's org.
        """
        return self._http.get(f"{self._audit}/decisions/{path_segment(decision_id, 'decision_id')}/receipt")

    def get_receipt(self, row_id: str) -> dict[str, Any]:
        """Fetch the Decision Receipt for an audit row id. GET .../audit/:rowId/receipt."""
        return self._http.get(f"{self._audit}/{path_segment(row_id, 'row_id')}/receipt")

    def request_package(
        self,
        *,
        from_date: str | None = None,
        to_date: str | None = None,
        ai_system_id: str | None = None,
    ) -> dict[str, Any]:
        """Queue an audit package export. POST .../audit/packages (202, returns the job).

        ``from_date``/``to_date`` bound the included audit bundle (server default: the
        90 days ending now). ``ai_system_id`` narrows inventory, risk and incidents.
        Poll :meth:`get_package` until ``status`` is ``done``, then :meth:`download_package`.
        """
        evidence_date_range(from_date, to_date)
        body: dict[str, Any] = {}
        if from_date is not None:
            body["from"] = from_date
        if to_date is not None:
            body["to"] = to_date
        if ai_system_id is not None:
            body["aiSystemId"] = _uuid(ai_system_id, "ai_system_id")
        return self._http.post(f"{self._audit}/packages", json=body)

    def get_package(self, package_id: str) -> dict[str, Any]:
        """Return the package job (``status``: queued/running/done/failed). GET .../audit/packages/:id."""
        return self._http.get(f"{self._audit}/packages/{_uuid(package_id, 'package_id')}")

    def download_package(self, package_id: str) -> bytes:
        """Download a finished audit package ZIP through the bounded (128 MiB) transport.

        Raises ``PraesidiaError`` with ``status_code`` 409 while the job is not done
        and 410 once the package is past its 7-day retention. A download does not
        verify the evidence: run ``praesidia-verify`` on it.
        """
        path = f"{self._audit}/packages/{_uuid(package_id, 'package_id')}/download"
        response = self._http.stream_get(path)
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
