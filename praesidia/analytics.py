"""
Praesidia SDK — AnalyticsResource.

Covers the ``/organizations/{org_id}/analytics`` advanced endpoints.
"""

from __future__ import annotations

from typing import Any

from ._http import HttpClient, path_segment


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
        return self._http.get(f"{self._base}/advanced/cost-trends", params=params)

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
        return self._http.get(
            f"{self._base}/advanced/agent-performance", params=params or None
        )

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
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 100
        ):
            raise ValueError("limit must be an integer from 1 to 100")
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

    # ------------------------------------------------------------------
    # AUD-0063 — closes the 9/10-route gap vs be's AnalyticsController.
    # Every method below is TS-parity with `PraesidiaAnalytics` (sdk/src/
    # analytics.ts) — identical endpoint, identical semantics.
    # ------------------------------------------------------------------

    def capture_state(self) -> dict[str, Any]:
        """
        Return the org's analytics-capture configuration.

        Calls ``GET /organizations/{org_id}/analytics/capture-state``.
        Idempotent GET -- retried per policy (R-SDK-1 does not apply; this
        is not a write).

        Returns:
            ``{enabled, piiCapture, sampleRate, retentionDays}``.
        """
        return self._http.get(f"{self._base}/capture-state")

    def agent_analytics(self, agent_id: str, days: int = 30) -> dict[str, Any]:
        """
        Return the analytics breakdown for a single agent.

        Calls ``GET /organizations/{org_id}/analytics/agents/{agent_id}``.

        Args:
            agent_id: UUID of the agent.
            days:     Rolling window in days (1..365, default: 30).

        Returns:
            Agent analytics dict.
        """
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
            raise ValueError("days must be an integer from 1 to 365")
        return self._http.get(
            f"{self._base}/agents/{path_segment(agent_id, 'agent_id')}",
            params={"days": days},
        )

    def events(
        self,
        agent_id: str | None = None,
        event_type: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Return a page of raw analytics events.

        Calls ``GET /organizations/{org_id}/analytics/events``. Unwraps the
        ``{data, meta}`` pagination envelope, matching ``agents.list`` /
        ``audit.list``. Page/limit are NOT validated client-side (matching
        ``audit.list``/``audit.stream``'s existing convention) -- be-core's
        ``PaginationDto`` enforces `[1, 100]` server-side.

        Args:
            agent_id:   Filter to one agent (optional).
            event_type: Filter by event type -- one of REQUEST, RESPONSE,
                        ERROR, TOKEN_ISSUED, GUARDRAIL_TRIGGERED (optional).
            from_date:  ISO 8601 start date (optional).
            to_date:    ISO 8601 end date (optional).
            page:       1-based page number (default: 1).
            limit:      Maximum results per page (default: 20).

        Returns:
            A list of analytics event dicts.
        """
        params: dict[str, Any] = {"page": page, "limit": limit}
        if agent_id is not None:
            params["agentId"] = agent_id
        if event_type is not None:
            params["eventType"] = event_type
        if from_date is not None:
            params["startDate"] = from_date
        if to_date is not None:
            params["endDate"] = to_date
        result = self._http.get(f"{self._base}/events", params=params)
        if isinstance(result, list):
            return result
        return result.get("data", [])

    def activity_log(
        self,
        agent_id: str | None = None,
        event_type: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Identical semantics to :meth:`events` -- be-core exposes this alias
        (PRA-QA-261) because some browser privacy-extension tracker lists
        block XHR paths ending in ``/analytics/events``; it delegates
        server-side to the same validated, permission-gated handler.

        Calls ``GET /organizations/{org_id}/analytics/activity-log``.
        """
        params: dict[str, Any] = {"page": page, "limit": limit}
        if agent_id is not None:
            params["agentId"] = agent_id
        if event_type is not None:
            params["eventType"] = event_type
        if from_date is not None:
            params["startDate"] = from_date
        if to_date is not None:
            params["endDate"] = to_date
        result = self._http.get(f"{self._base}/activity-log", params=params)
        if isinstance(result, list):
            return result
        return result.get("data", [])

    def record_event(
        self,
        event_type: str,
        agent_id: str | None = None,
        endpoint: str | None = None,
        method: str | None = None,
        status_code: int | None = None,
        response_time_ms: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Record an analytics event.

        Calls ``POST /organizations/{org_id}/analytics/events``.

        Retry classification (R-SDK-1): this path is NOT in be-core's
        Idempotency-Key-honoured allowlist (only ``POST .../tasks`` and the
        A2A task routes are), so this is a bare, never-retried POST -- a
        transient 5xx surfaces to the caller instead of risking a
        double-recorded event. This route is also gated on
        ``ANALYTICS_CREATE`` (distinct from the read-only ``ANALYTICS_VIEW``
        every other method on this resource needs) and has no mintable
        API-key scope in be-core's taxonomy -- authenticate with a JWT
        bearer, not an API key, for this call.

        Args:
            event_type:       One of REQUEST, RESPONSE, ERROR, TOKEN_ISSUED,
                               GUARDRAIL_TRIGGERED.
            agent_id:         Agent the event is attributed to (optional).
            endpoint:         Request endpoint path (optional).
            method:           HTTP method (optional).
            status_code:      HTTP status code (optional).
            response_time_ms: Response time in milliseconds (optional).
            error_code:       Machine-readable error code (optional).
            error_message:    Human-readable error message (optional).
            metadata:         Free-form context, bounded server-side to 4096
                               bytes serialized / 5 levels deep (AUDIT-021).

        Returns:
            The created analytics event dict.
        """
        body: dict[str, Any] = {"eventType": event_type}
        if agent_id is not None:
            body["agentId"] = agent_id
        if endpoint is not None:
            body["endpoint"] = endpoint
        if method is not None:
            body["method"] = method
        if status_code is not None:
            body["statusCode"] = status_code
        if response_time_ms is not None:
            body["responseTimeMs"] = response_time_ms
        if error_code is not None:
            body["errorCode"] = error_code
        if error_message is not None:
            body["errorMessage"] = error_message
        if metadata is not None:
            body["metadata"] = metadata
        return self._http.post(f"{self._base}/events", json=body)

    def security_metrics(
        self,
        days: int = 30,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        """
        Return security metrics (failed auth, rate limits, risk score).

        Calls ``GET /organizations/{org_id}/analytics/advanced/security``
        (ADVANCED_ANALYTICS).

        Args:
            days:      Rolling window in days (1..365, default: 30).
            from_date: Optional ISO 8601 start date.
            to_date:   Optional ISO 8601 end date.
        """
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
            raise ValueError("days must be an integer from 1 to 365")
        params: dict[str, Any] = {"days": days}
        if from_date is not None:
            params["startDate"] = from_date
        if to_date is not None:
            params["endDate"] = to_date
        return self._http.get(f"{self._base}/advanced/security", params=params)

    def usage_heatmap(
        self,
        days: int = 30,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        """
        Return activity heatmap by hour / day-of-week.

        Calls ``GET /organizations/{org_id}/analytics/advanced/usage-heatmap``
        (ADVANCED_ANALYTICS).

        Args:
            days:      Rolling window in days (1..365, default: 30).
            from_date: Optional ISO 8601 start date.
            to_date:   Optional ISO 8601 end date.
        """
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
            raise ValueError("days must be an integer from 1 to 365")
        params: dict[str, Any] = {"days": days}
        if from_date is not None:
            params["startDate"] = from_date
        if to_date is not None:
            params["endDate"] = to_date
        return self._http.get(f"{self._base}/advanced/usage-heatmap", params=params)

    def compliance_metrics(
        self,
        days: int = 30,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        """
        Return policy/guardrail/access-review compliance metrics.

        Calls ``GET /organizations/{org_id}/analytics/advanced/compliance``
        (ADVANCED_ANALYTICS).

        Args:
            days:      Rolling window in days (1..365, default: 30).
            from_date: Optional ISO 8601 start date.
            to_date:   Optional ISO 8601 end date.
        """
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
            raise ValueError("days must be an integer from 1 to 365")
        params: dict[str, Any] = {"days": days}
        if from_date is not None:
            params["startDate"] = from_date
        if to_date is not None:
            params["endDate"] = to_date
        return self._http.get(f"{self._base}/advanced/compliance", params=params)

    def anomalies(self, days: int = 7) -> list[dict[str, Any]]:
        """
        Return connections whose error rate/latency is >2 std-dev above the
        organisation mean.

        Calls ``GET /organizations/{org_id}/analytics/advanced/anomalies``
        (ADVANCED_ANALYTICS). Default window is 7 days -- matches be's
        ``DefaultValuePipe(7)`` (narrower than every other ``days`` default
        on this resource, which default to 30).

        Args:
            days: Rolling window in days (1..365, default: 7).
        """
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
            raise ValueError("days must be an integer from 1 to 365")
        return self._http.get(f"{self._base}/advanced/anomalies", params={"days": days})

    def cost_by_team(self, days: int = 30) -> list[dict[str, Any]]:
        """
        Return cost allocation by team.

        Calls ``GET /organizations/{org_id}/analytics/advanced/cost-by-team``
        (ADVANCED_ANALYTICS).

        Args:
            days: Rolling window in days (1..365, default: 30).
        """
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
            raise ValueError("days must be an integer from 1 to 365")
        return self._http.get(f"{self._base}/advanced/cost-by-team", params={"days": days})

    def model_comparison(self, days: int = 30) -> list[dict[str, Any]]:
        """
        Return per-model cost/latency/success-rate comparison.

        Calls ``GET /organizations/{org_id}/analytics/advanced/model-comparison``
        (ADVANCED_ANALYTICS).

        Args:
            days: Rolling window in days (1..365, default: 30).
        """
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
            raise ValueError("days must be an integer from 1 to 365")
        return self._http.get(
            f"{self._base}/advanced/model-comparison", params={"days": days}
        )
