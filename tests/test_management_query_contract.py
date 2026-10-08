"""Replay the app's list-query contract in both SDKs, including pagination."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import httpx
import pytest
import respx

from praesidia import Praesidia
from praesidia.ai_systems import AiSystemsResource

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "test-fixtures/management-query-v1.json").read_text())
BASE = "https://test.local/organizations/org-1"


def client():
    return Praesidia(api_key="pk_test", org_id="org-1", base_url="https://test.local")


def arguments(query):
    result = {re.sub(r"[A-Z]", lambda m: "_" + m[0].lower(), k): v for k, v in query.items()}
    if "include_archived" in result:
        result["include_archived"] = result["include_archived"] == "true"
    return result


@respx.mock
@pytest.mark.parametrize("route", FIXTURE)
def test_forwards_every_backend_filter(route):
    c = client()
    calls = {
        "agents": c.agents.list,
        "connections": c.connections.list,
        "workflows": c.workflows.list,
        "asset-relationships": c.ai_systems.list_relationships,
        "ai-systems/lifecycle-requests": c.ai_systems.list_lifecycle_requests,
    }
    req = respx.get(f"{BASE}/{route}").mock(return_value=httpx.Response(200, json={"data": [{"id": "row-1"}]}))
    assert calls[route](**arguments(FIXTURE[route]["query"])) == [{"id": "row-1"}]
    assert dict(req.calls.last.request.url.params) == {k: str(v) for k, v in FIXTURE[route]["query"].items()}


@respx.mock
@pytest.mark.parametrize("resource", ["agents", "connections", "workflows", "asset-relationships"])
def test_preserves_filters_on_every_page(resource):
    c = client()
    req = respx.get(f"{BASE}/{resource}").mock(side_effect=[
        httpx.Response(200, json={"data": [{"id": "1"}]}),
        httpx.Response(200, json={"data": [{"id": "2"}]}),
        httpx.Response(200, json={"data": []}),
    ])
    call = c.ai_systems.list_relationships_all if resource == "asset-relationships" else getattr(c, resource).list_all
    query = {k: v for k, v in FIXTURE[resource]["query"].items() if k != "page"}
    assert list(call(**arguments(query))) == [{"id": "1"}, {"id": "2"}]
    for page, request in enumerate(req.calls, start=1):
        assert dict(request.request.url.params) == {"page": str(page), **{k: str(v) for k, v in query.items()}}


@respx.mock
@pytest.mark.parametrize("resource,key", [
    ("agents", "role"), ("agents", "status"), ("agents", "visibility"),
    ("agents", "tier"), ("agents", "scope"), ("connections", "status"),
    ("workflows", "status"), ("ai_systems", "cross_border_status"),
])
def test_rejects_invalid_enum_before_request(resource, key):
    c = client()
    call = c.ai_systems.list_relationships if resource == "ai_systems" else getattr(c, resource).list
    with pytest.raises(ValueError, match=key):
        call(**{key: "invalid"})
    assert not respx.calls


@respx.mock
@pytest.mark.parametrize("resource", ["agents", "connections", "workflows"])
def test_optional_none_filter_is_omitted_and_unknown_filters_fail(resource):
    req = respx.get(f"{BASE}/{resource}").mock(return_value=httpx.Response(200, json={"data": []}))
    call = getattr(client(), resource).list_page
    call(status=None)
    assert dict(req.calls.last.request.url.params) == {"page": "1", "limit": "20"}
    with pytest.raises(ValueError, match="Unknown list filter"):
        call(unsupported="value")
    assert req.call_count == 1


@respx.mock
def test_lifecycle_queue_defaults_and_bare_response():
    req = respx.get(f"{BASE}/ai-systems/lifecycle-requests").mock(return_value=httpx.Response(200, json=[{"id": "pending"}]))
    assert client().ai_systems.list_lifecycle_requests() == [{"id": "pending"}]
    assert "status" not in req.calls.last.request.url.params
    with pytest.raises(ValueError, match="status"):
        client().ai_systems.list_lifecycle_requests(status="invalid")
    assert req.call_count == 1


@respx.mock
@pytest.mark.parametrize("method,route", [("agent_performance", "agent-performance"), ("top_agents", "top-agents")])
def test_analytics_rolling_window(method, route):
    req = respx.get(f"{BASE}/analytics/advanced/{route}").mock(return_value=httpx.Response(200, json={}))
    call = getattr(client().analytics, method)
    call(days=7)
    assert req.calls.last.request.url.params["days"] == "7"
    for days in [True, 0, 366, 1.5]:
        with pytest.raises(ValueError, match="days"):
            call(days=days)
    assert req.call_count == 1


SPEC = Path(os.environ.get("BE_SWAGGER_PATH", ROOT.parent / "be/openapi.json"))


@pytest.mark.skipif(not SPEC.is_file() and os.environ.get("REQUIRE_SWAGGER") != "1", reason="Backend OpenAPI spec is not available")
def test_fixture_covers_current_backend_query_fields_and_enums():
    spec = json.loads(SPEC.read_text())
    for route, contract in FIXTURE.items():
        params = {p["name"]: p["schema"] for p in spec["paths"][f"/organizations/{{orgId}}/{route}"]["get"]["parameters"] if p["in"] == "query"}
        assert set(params) == set(contract["query"]), route
        for name, values in contract["enums"].items():
            schema = params[name]
            if "$ref" in schema:
                schema = spec["components"]["schemas"][schema["$ref"].split("/")[-1]]
            assert set(schema["enum"]) == set(values), (route, name)
    assert set(AiSystemsResource.CROSS_BORDER_STATUSES) == set(FIXTURE["asset-relationships"]["enums"]["crossBorderStatus"])
    assert set(AiSystemsResource.LIFECYCLE_REQUEST_STATUSES) == set(FIXTURE["ai-systems/lifecycle-requests"]["enums"]["status"])
