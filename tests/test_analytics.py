"""Backend-contract tests for the Python analytics resource."""

from __future__ import annotations

import json

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


# ---------------------------------------------------------------------------
# AUD-0063 — new routes: path encoding, retry classification, defaults
# ---------------------------------------------------------------------------


@respx.mock
def test_agent_analytics_percent_encodes_the_agent_id_path_segment():
    route = respx.get(f"{ANALYTICS}/agents/agent%2F..%2Fevil").mock(
        return_value=httpx.Response(200, json={})
    )

    _client().analytics.agent_analytics("agent/../evil", days=5)

    assert route.called
    assert dict(route.calls.last.request.url.params) == {"days": "5"}


def test_agent_analytics_rejects_a_bare_dot_dot_agent_id():
    with pytest.raises(ValueError, match="agent_id"):
        _client().analytics.agent_analytics("..")


@respx.mock
def test_anomalies_defaults_to_a_seven_day_window():
    route = respx.get(f"{ANALYTICS}/advanced/anomalies").mock(
        return_value=httpx.Response(200, json=[])
    )

    _client().analytics.anomalies()

    assert dict(route.calls.last.request.url.params) == {"days": "7"}


@pytest.mark.parametrize(
    "method_name,path_suffix",
    [("cost_by_team", "cost-by-team"), ("model_comparison", "model-comparison")],
)
def test_cost_by_team_and_model_comparison_default_to_a_thirty_day_window(
    method_name, path_suffix
):
    with respx.mock:
        route = respx.get(f"{ANALYTICS}/advanced/{path_suffix}").mock(
            return_value=httpx.Response(200, json=[])
        )
        getattr(_client().analytics, method_name)()
        assert dict(route.calls.last.request.url.params) == {"days": "30"}


@respx.mock
def test_capture_state_gets_with_no_query():
    route = respx.get(f"{ANALYTICS}/capture-state").mock(
        return_value=httpx.Response(
            200,
            json={
                "enabled": True,
                "piiCapture": False,
                "sampleRate": 1,
                "retentionDays": 90,
            },
        )
    )

    result = _client().analytics.capture_state()

    assert result == {
        "enabled": True,
        "piiCapture": False,
        "sampleRate": 1,
        "retentionDays": 90,
    }
    assert not route.calls.last.request.url.params


@respx.mock
def test_events_unwraps_the_data_meta_pagination_envelope():
    route = respx.get(f"{ANALYTICS}/events").mock(
        return_value=httpx.Response(
            200, json={"data": [{"id": "evt-1"}], "total": 1, "meta": {"page": 2}}
        )
    )

    events = _client().analytics.events(agent_id="agent-1", page=2, limit=10)

    assert events == [{"id": "evt-1"}]
    assert dict(route.calls.last.request.url.params) == {
        "agentId": "agent-1",
        "page": "2",
        "limit": "10",
    }


@respx.mock
def test_events_accepts_a_bare_array_response():
    respx.get(f"{ANALYTICS}/events").mock(
        return_value=httpx.Response(200, json=[{"id": "evt-legacy"}])
    )

    events = _client().analytics.events(from_date="2026-07-01", to_date="2026-07-31")

    assert events == [{"id": "evt-legacy"}]


@respx.mock
def test_activity_log_hits_the_pra_qa_261_alias_path():
    route = respx.get(f"{ANALYTICS}/activity-log").mock(
        return_value=httpx.Response(200, json={"data": []})
    )

    _client().analytics.activity_log(event_type="ERROR")

    # page/limit are always sent (matches agents.list/events's own established
    # Python-side convention of unconditional defaults; be's PaginationDto
    # defaults to the same values, so this is wire-equivalent to the TS SDK
    # omitting them).
    assert dict(route.calls.last.request.url.params) == {
        "page": "1",
        "limit": "20",
        "eventType": "ERROR",
    }
    assert route.called


@respx.mock
def test_record_event_posts_the_body_and_is_a_bare_never_retried_write():
    route = respx.post(f"{ANALYTICS}/events").mock(
        return_value=httpx.Response(200, json={"id": "evt-new", "eventType": "REQUEST"})
    )

    result = _client().analytics.record_event("REQUEST", agent_id="agent-1")

    assert result == {"id": "evt-new", "eventType": "REQUEST"}
    request = route.calls.last.request
    assert json.loads(request.content) == {"eventType": "REQUEST", "agentId": "agent-1"}
    # `record_event` exposes no idempotency_key parameter -- this route is not
    # in be-core's Idempotency-Key allowlist (only POST .../tasks + A2A task
    # routes are), so it is correctly a bare, never-retried POST. Generic
    # bare-POST-never-retried behavior is proven once, client-wide, in
    # test_http.py / test_retry.py.
    assert "Idempotency-Key" not in request.headers


@respx.mock
def test_security_usage_heatmap_compliance_send_the_full_window_query():
    sec_route = respx.get(f"{ANALYTICS}/advanced/security").mock(
        return_value=httpx.Response(200, json={})
    )
    heat_route = respx.get(f"{ANALYTICS}/advanced/usage-heatmap").mock(
        return_value=httpx.Response(200, json={})
    )
    comp_route = respx.get(f"{ANALYTICS}/advanced/compliance").mock(
        return_value=httpx.Response(200, json={})
    )

    client = _client()
    client.analytics.security_metrics(days=14)
    client.analytics.usage_heatmap(from_date="2026-07-01", to_date="2026-07-14")
    client.analytics.compliance_metrics(days=60)

    assert dict(sec_route.calls.last.request.url.params) == {"days": "14"}
    # `days` is always sent (defaults to 30) — matches cost_trends's existing
    # convention; be's AdvancedAnalyticsQueryDto.days also defaults to 30, so
    # this is wire-equivalent to the TS SDK omitting an unset `days`.
    assert dict(heat_route.calls.last.request.url.params) == {
        "days": "30",
        "startDate": "2026-07-01",
        "endDate": "2026-07-14",
    }
    assert dict(comp_route.calls.last.request.url.params) == {"days": "60"}
