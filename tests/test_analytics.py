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

    result = _client().analytics.export(from_date="2026-07-01")

    assert result == b"id,createdAt\n"
    assert dict(route.calls.last.request.url.params) == {
        "startDate": "2026-07-01"
    }
