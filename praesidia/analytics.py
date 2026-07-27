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
        days: int = 30,
    ) -> dict[str, Any]:
        """
        Return aggregate usage metrics for a rolling day window.

        Calls ``GET /organizations/{org_id}/analytics``.

        Args:
            days: Rolling window in days (1..365, default: 30).

        Returns:
            Usage summary dict (task counts, token totals, etc.).
        """
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
            raise ValueError("days must be an integer from 1 to 365")
        return self._http.get(self._base, params={"days": days})

    def cost_trends(
        self,
        days: int = 30,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        """
        Return cost-over-time data for the specified rolling period.

        Calls ``GET /organizations/{org_id}/analytics/advanced/cost-trends``.

        Args:
            days:      Rolling window in days (1..365, default: 30).
            from_date: Optional ISO 8601 start date.
            to_date:   Optional ISO 8601 end date.

        Returns:
            Cost trend dict with time-series data points.
        """
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
            raise ValueError("days must be an integer from 1 to 365")
        params: dict[str, Any] = {"days": days}
        if from_date is not None:
            params["startDate"] = from_date
        if to_date is not None:
            params["endDate"] = to_date
        return self._http.get(
            f"{self._base}/advanced/cost-trends", params=params
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
    ) -> bytes:
        """
        Export analytics data in bulk.

        Calls ``GET /organizations/{org_id}/analytics/export``.

        Args:
            from_date: ISO 8601 start date (optional).
            to_date:   ISO 8601 end date (optional).
        Returns:
            Raw CSV export bytes.
        """
        params: dict[str, Any] = {}
        if from_date is not None:
            params["startDate"] = from_date
        if to_date is not None:
            params["endDate"] = to_date
        r = self._http.stream_get(f"{self._base}/export", params=params)
        self._http._raise_for_status(r)
        return r.content
