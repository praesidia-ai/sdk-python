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
GET_AGENT = f"{BASE_URL}/organizations/{ORG_ID}/agents/{AGENT_ID}"
TASKS_URL = f"{BASE_URL}/organizations/{ORG_ID}/tasks"


def _client() -> Praesidia:
    return Praesidia(api_key="sk-test", org_id=ORG_ID, base_url=BASE_URL)


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

    calls = respx.calls
    assert calls[0].request.headers["X-API-Key"] == "sk-test"
    assert calls[1].request.headers["X-API-Key"] == "sec_new_9f8e"


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

    calls = respx.calls
    assert calls[0].request.headers["X-API-Key"] == "sk-test"
    assert calls[1].request.headers["X-API-Key"] == "sk-rotated"


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


def test_run_rejects_empty_input():
    with pytest.raises(ValueError):
        _client().agents.run(CONN_ID, {})


def test_run_rejects_invalid_type():
    with pytest.raises(ValueError):
        _client().agents.run(CONN_ID, {"message": "hi"}, type="BOGUS")
