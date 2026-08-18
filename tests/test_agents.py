"""Tests for praesidia.agents runtime credential refresh (Q4-01) and the
AUDIT-SDK-02 task-submit contract."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from praesidia import Praesidia

BASE_URL = "http://test.local"
ORG_ID = "org-1"
AGENT_ID = "agent-9"
CONN_ID = "00000000-0000-4000-8000-000000000c01"
PARENT_TASK_ID = "22222222-2222-4222-8222-222222222222"
GET_AGENT = f"{BASE_URL}/organizations/{ORG_ID}/agents/{AGENT_ID}"
AGENTS_URL = f"{BASE_URL}/organizations/{ORG_ID}/agents"
TASKS_URL = f"{BASE_URL}/organizations/{ORG_ID}/tasks"


def _client() -> Praesidia:
    return Praesidia(api_key="sk-test", org_id=ORG_ID, base_url=BASE_URL)


@respx.mock
def test_agent_crud_routes_and_pagination_envelopes():
    list_route = respx.get(AGENTS_URL).mock(
        return_value=httpx.Response(200, json={"agents": [{"id": AGENT_ID}]})
    )
    get_route = respx.get(GET_AGENT).mock(
        return_value=httpx.Response(200, json={"id": AGENT_ID})
    )
    create_route = respx.post(AGENTS_URL).mock(
        return_value=httpx.Response(201, json={"id": AGENT_ID})
    )
    update_route = respx.patch(GET_AGENT).mock(
        return_value=httpx.Response(200, json={"id": AGENT_ID, "name": "renamed"})
    )
    delete_route = respx.delete(GET_AGENT).mock(return_value=httpx.Response(204))

    client = _client()
    assert client.agents.list(page=2, limit=25) == [{"id": AGENT_ID}]
    assert dict(list_route.calls.last.request.url.params) == {
        "page": "2",
        "limit": "25",
    }
    assert client.agents.get(AGENT_ID)["id"] == AGENT_ID
    assert get_route.called
    assert client.agents.create({"name": "bot"})["id"] == AGENT_ID
    assert json.loads(create_route.calls.last.request.content) == {"name": "bot"}
    assert client.agents.update(AGENT_ID, {"name": "renamed"})["name"] == "renamed"
    assert json.loads(update_route.calls.last.request.content) == {"name": "renamed"}
    assert client.agents.delete(AGENT_ID) is None
    assert delete_route.called


@respx.mock
def test_agent_list_handles_bare_list_and_data_envelopes():
    route = respx.get(AGENTS_URL).mock(
        side_effect=[
            httpx.Response(200, json=[{"id": "bare"}]),
            httpx.Response(200, json={"data": [{"id": "data"}]}),
        ]
    )

    client = _client()
    assert client.agents.list() == [{"id": "bare"}]
    assert client.agents.list() == [{"id": "data"}]
    assert route.call_count == 2


@respx.mock
def test_refresh_credential_swaps_auth_header():
    respx.get(GET_AGENT).mock(
        side_effect=[
            httpx.Response(200, json={"id": AGENT_ID}),
            httpx.Response(200, json={"id": AGENT_ID}),
        ]
    )
    client = _client()
    client.agents.get(AGENT_ID)

    # Adopt a freshly provisioned secret in-process, then call again.
    client.agents.refresh_credential("sec_new_9f8e")
    client.agents.get(AGENT_ID)

    # AUDIT-SDK-04 — the SDK authenticates with Authorization: Bearer <key>.
    calls = respx.calls
    assert calls[0].request.headers["Authorization"] == "Bearer sk-test"
    assert calls[1].request.headers["Authorization"] == "Bearer sec_new_9f8e"


@respx.mock
def test_client_level_refresh_credential_swaps_auth_header():
    respx.get(GET_AGENT).mock(
        side_effect=[
            httpx.Response(200, json={"id": AGENT_ID}),
            httpx.Response(200, json={"id": AGENT_ID}),
        ]
    )
    client = _client()
    client.agents.get(AGENT_ID)
    client.refresh_credential("sk-rotated")
    client.agents.get(AGENT_ID)

    # AUDIT-SDK-04 — the SDK authenticates with Authorization: Bearer <key>.
    calls = respx.calls
    assert calls[0].request.headers["Authorization"] == "Bearer sk-test"
    assert calls[1].request.headers["Authorization"] == "Bearer sk-rotated"


# ---------------------------------------------------------------------------
# AUDIT-SDK-02 — task submit sends a CreateAgentTaskDto-valid body
# ---------------------------------------------------------------------------


@respx.mock
def test_run_posts_create_agent_task_dto_body():
    route = respx.post(TASKS_URL).mock(
        return_value=httpx.Response(201, json={"id": "task-1", "status": "PENDING"})
    )
    result = _client().agents.run(CONN_ID, {"message": "hi"}, type="MESSAGE")

    assert result["id"] == "task-1"
    assert route.called
    body = json.loads(route.calls[0].request.content)
    # Contract: CreateAgentTaskDto requires connectionId (UUID), type (enum),
    # and a non-empty input OBJECT. The old {"agentId", "input"} body 400'd.
    assert body["connectionId"] == CONN_ID
    assert body["type"] == "MESSAGE"
    assert body["input"] == {"message": "hi"}
    # agentId is NOT a task field — whitelist ValidationPipe would reject it.
    assert "agentId" not in body


@respx.mock
def test_run_forwards_uuid_chain_id_in_body():
    route = respx.post(TASKS_URL).mock(
        return_value=httpx.Response(201, json={"id": "t"})
    )
    chain = "11111111-1111-4111-8111-111111111111"
    _client().agents.run(CONN_ID, {"message": "hi"}, chain_id=chain)
    body = json.loads(route.calls[0].request.content)
    assert body["chainId"] == chain


@respx.mock
def test_run_sends_all_optional_create_agent_task_fields():
    route = respx.post(TASKS_URL).mock(
        return_value=httpx.Response(201, json={"id": "t"})
    )

    _client().agents.run(
        CONN_ID,
        {"tool": "search"},
        type="TOOL_CALL",
        dry_run=True,
        callback_url="https://example.test/callback",
        parent_task_id=PARENT_TASK_ID,
    )

    assert json.loads(route.calls.last.request.content) == {
        "connectionId": CONN_ID,
        "type": "TOOL_CALL",
        "input": {"tool": "search"},
        "dryRun": True,
        "callbackUrl": "https://example.test/callback",
        "parentTaskId": PARENT_TASK_ID,
    }


def test_run_rejects_empty_input():
    with pytest.raises(ValueError):
        _client().agents.run(CONN_ID, {})


def test_run_rejects_invalid_type():
    with pytest.raises(ValueError):
        _client().agents.run(CONN_ID, {"message": "hi"}, type="BOGUS")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"connection_id": "not-a-uuid"}, "connection_id"),
        ({"chain_id": "not-a-uuid"}, "chain_id"),
        ({"chain_id": 123}, "chain_id"),
        ({"parent_task_id": "not-a-uuid"}, "parent_task_id"),
        ({"parent_task_id": 123}, "parent_task_id"),
    ],
)
def test_run_rejects_invalid_identifiers_before_network(kwargs, message):
    kwargs = dict(kwargs)
    connection_id = kwargs.pop("connection_id", CONN_ID)
    with pytest.raises(ValueError, match=message):
        _client().agents.run(connection_id, {"message": "hi"}, **kwargs)
