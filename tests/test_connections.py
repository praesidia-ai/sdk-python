"""Backend-contract tests for every Python connection SDK method."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from praesidia import Praesidia

BASE_URL = "http://test.local"
ORG_ID = "org-1"
BASE = f"{BASE_URL}/organizations/{ORG_ID}/connections"


def _client() -> Praesidia:
    return Praesidia(api_key="sk-test", org_id=ORG_ID, base_url=BASE_URL)


@respx.mock
def test_connection_list_get_and_create_contracts():
    respx.get(BASE).mock(return_value=httpx.Response(200, json={"data": [{"id": "c"}]}))
    assert _client().connections.list(page=2, limit=7) == [{"id": "c"}]
    assert dict(respx.calls.last.request.url.params) == {"page": "2", "limit": "7"}

    respx.get(f"{BASE}/c").mock(return_value=httpx.Response(200, json={"id": "c"}))
    assert _client().connections.get("c")["id"] == "c"

    agent = respx.post(f"{BASE}/agent").mock(
        return_value=httpx.Response(201, json={"id": "a"})
    )
    _client().connections.create_agent({"clientAgentId": "a"})
    assert json.loads(agent.calls.last.request.content) == {"clientAgentId": "a"}

    mcp = respx.post(f"{BASE}/mcp").mock(
        return_value=httpx.Response(201, json={"id": "m"})
    )
    _client().connections.create_mcp({"mcpServerId": "m"})
    assert json.loads(mcp.calls.last.request.content) == {"mcpServerId": "m"}


@respx.mock
def test_connection_lifecycle_routes_and_status_enum():
    status = respx.patch(f"{BASE}/c/status").mock(
        return_value=httpx.Response(200, json={"status": "ACTIVE"})
    )
    _client().connections.update_status("c", "ACTIVE")
    assert json.loads(status.calls.last.request.content) == {"status": "ACTIVE"}

    test_route = respx.post(f"{BASE}/c/test").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    assert _client().connections.test("c")["success"] is True
    assert test_route.called

    respx.get(f"{BASE}/c/health").mock(
        return_value=httpx.Response(200, json={"healthy": True})
    )
    assert _client().connections.health("c")["healthy"] is True

    delete = respx.delete(f"{BASE}/c").mock(return_value=httpx.Response(204))
    _client().connections.delete("c")
    assert delete.called


@pytest.mark.parametrize("status", ["active", "INACTIVE", "", None])
def test_connection_status_rejects_values_the_backend_enum_rejects(status):
    with pytest.raises(ValueError, match="status"):
        _client().connections.update_status("c", status)
