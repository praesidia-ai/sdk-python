"""
Praesidia SDK — AnalyticsResource.

Covers the ``/organizations/{org_id}/analytics`` advanced endpoints.
"""

from __future__ import annotations

from typing import Any

from ._http import HttpClient


class AnalyticsResource:
    """
    Query platform analytics and cost data for an organisation.

    Endpoint base: ``/organizations/{org_id}/analytics``
    """

    def __init__(self, http: HttpClient) -> None:
        self._http = http
        self._base = f"/organizations/{http.org_id}/analytics"

    def usage(
        self,
        from_date: str,
        to_date: str,
        agent_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Return aggregate usage metrics for a date range.

        Calls ``GET /organizations/{org_id}/analytics``.

        Args:
            from_date: ISO 8601 start date (inclusive, e.g. ``"2026-01-01"``).
            to_date:   ISO 8601 end date (inclusive, e.g. ``"2026-01-31"``).
            agent_id:  Optional UUID to narrow to a single agent.

        Returns:
            Usage summary dict (task counts, token totals, etc.).
        """
        params: dict[str, Any] = {
            "startDate": from_date,
            "endDate": to_date,
        }
        if agent_id is not None:
            params["agentId"] = agent_id
        return self._http.get(self._base, params=params)

    def cost_trends(
        self,
        period: str = "30d",
    ) -> dict[str, Any]:
        """
        Return cost-over-time data for the specified rolling period.

        Calls ``GET /organizations/{org_id}/analytics/advanced/cost-trends``.

        Args:
            period: Rolling window — e.g. ``"7d"``, ``"30d"``, ``"90d"``
                    (default: ``"30d"``).

        Returns:
            Cost trend dict with time-series data points.
        """
        return self._http.get(
            f"{self._base}/advanced/cost-trends",
            params={"period": period},
        )

    def agent_performance(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        """
        Return per-agent performance breakdown.

        Calls ``GET /organizations/{org_id}/analytics/advanced/agent-performance``.

        Args:
            from_date: ISO 8601 start date (optional).
            to_date:   ISO 8601 end date (optional).

        Returns:
            Agent performance dict.
        """
        params: dict[str, Any] = {}
        if from_date is not None:
            params["startDate"] = from_date
        if to_date is not None:
            params["endDate"] = to_date
        return self._http.get(f"{self._base}/advanced/agent-performance", params=params or None)

    def top_agents(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """
        Return the top agents by task volume or cost.

        Calls ``GET /organizations/{org_id}/analytics/advanced/top-agents``.

        Args:
            from_date: ISO 8601 start date (optional).
            to_date:   ISO 8601 end date (optional).
            limit:     How many top agents to return (default: 10).

        Returns:
            Top-agents dict.
        """
        params: dict[str, Any] = {"limit": limit}
        if from_date is not None:
            params["startDate"] = from_date
        if to_date is not None:
            params["endDate"] = to_date
        return self._http.get(f"{self._base}/advanced/top-agents", params=params)

    def export(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        format: str = "json",
    ) -> bytes:
        """
        Export analytics data in bulk.

        Calls ``GET /organizations/{org_id}/analytics/export``.

        Args:
            from_date: ISO 8601 start date (optional).
            to_date:   ISO 8601 end date (optional).
            format:    ``"json"`` or ``"csv"`` (default: ``"json"``).

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
