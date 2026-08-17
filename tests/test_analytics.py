"""Backend-contract tests for the Python analytics resource."""

from __future__ import annotations

import httpx
import pytest
import respx

from praesidia import Praesidia

BASE_URL = "http://test.local"
ORG_ID = "org-1"
ANALYTICS = f"{BASE_URL}/organizations/{ORG_ID}/analytics"


def _client() -> Praesidia:
    return Praesidia(api_key="sk-test", org_id=ORG_ID, base_url=BASE_URL)


@respx.mock
def test_usage_sends_the_root_endpoints_days_contract():
    route = respx.get(ANALYTICS).mock(return_value=httpx.Response(200, json={}))

    _client().analytics.usage(days=14)

    assert dict(route.calls.last.request.url.params) == {"days": "14"}


@respx.mock
def test_cost_trends_sends_advanced_query_dto_fields():
    route = respx.get(f"{ANALYTICS}/advanced/cost-trends").mock(
        return_value=httpx.Response(200, json={})
    )

    _client().analytics.cost_trends(
        days=7, from_date="2026-07-01", to_date="2026-07-07"
    )

    assert dict(route.calls.last.request.url.params) == {
        "days": "7",
        "startDate": "2026-07-01",
        "endDate": "2026-07-07",
    }
    assert "period" not in route.calls.last.request.url.params


@respx.mock
def test_agent_performance_sends_optional_date_range():
    route = respx.get(f"{ANALYTICS}/advanced/agent-performance").mock(
        return_value=httpx.Response(200, json={"agents": []})
    )

    result = _client().analytics.agent_performance(
        from_date="2026-07-01", to_date="2026-07-07"
    )

    assert result == {"agents": []}
    assert dict(route.calls.last.request.url.params) == {
        "startDate": "2026-07-01",
        "endDate": "2026-07-07",
    }


@respx.mock
def test_agent_performance_omits_empty_query():
    route = respx.get(f"{ANALYTICS}/advanced/agent-performance").mock(
        return_value=httpx.Response(200, json={})
    )

    _client().analytics.agent_performance()

    assert not route.calls.last.request.url.params


@respx.mock
def test_top_agents_sends_advanced_query_dto_fields():
    route = respx.get(f"{ANALYTICS}/advanced/top-agents").mock(
        return_value=httpx.Response(200, json={"agents": []})
    )

    _client().analytics.top_agents(
        from_date="2026-07-01", to_date="2026-07-07", limit=25
    )

    assert dict(route.calls.last.request.url.params) == {
        "limit": "25",
        "startDate": "2026-07-01",
        "endDate": "2026-07-07",
    }


@pytest.mark.parametrize("limit", [0, 101, True, 1.5])
def test_top_agents_limit_fails_fast(limit):
    with pytest.raises(ValueError, match="limit"):
        _client().analytics.top_agents(limit=limit)


@pytest.mark.parametrize("days", [0, 366, True, 2.5])
def test_analytics_day_windows_fail_fast(days):
    with pytest.raises(ValueError, match="days"):
        _client().analytics.usage(days=days)
    with pytest.raises(ValueError, match="days"):
        _client().analytics.cost_trends(days=days)


@respx.mock
def test_analytics_export_matches_csv_only_backend_contract():
    route = respx.get(f"{ANALYTICS}/export").mock(
        return_value=httpx.Response(200, content=b"id,createdAt\n")
    )

    result = _client().analytics.export(from_date="2026-07-01", to_date="2026-07-31")

    assert result == b"id,createdAt\n"
    assert dict(route.calls.last.request.url.params) == {
        "startDate": "2026-07-01",
        "endDate": "2026-07-31",
    }
