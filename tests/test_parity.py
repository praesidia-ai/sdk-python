"""Tests for the deferred SDK parity items (Q3-02 / Q4-02 / Q4-05).

Covers chain-trace forwarding, JIT capability-token forwarding on MCP tool
calls, and null-secret / JIT-first (403) handling.
"""

from __future__ import annotations

import json

import httpx
import respx

from praesidia import Praesidia, tool_call_headers_from_task

BASE_URL = "http://test.local"
ORG_ID = "org-1"
AGENT_ID = "agent-9"


def _client() -> Praesidia:
    return Praesidia(api_key="sk-test", org_id=ORG_ID, base_url=BASE_URL)


# ---------------------------------------------------------------------------
# Q3-02 — chainId forwarding
# ---------------------------------------------------------------------------


@respx.mock
def test_run_sends_chain_id_body_and_forwards_header():
    # AUDIT-SDK-02 — connectionId + chainId are UUIDs (CreateAgentTaskDto).
    conn = "00000000-0000-4000-8000-000000000c01"
    chain = "11111111-1111-4111-8111-111111111111"
    tasks_url = f"{BASE_URL}/organizations/{ORG_ID}/tasks"
    route = respx.post(tasks_url).mock(
        side_effect=[
            httpx.Response(201, json={"id": "task-1", "status": "PENDING"}),
            httpx.Response(201, json={"id": "task-2", "status": "PENDING"}),
        ]
    )
    client = _client()
    client.agents.run(conn, input={"message": "hi"}, chain_id=chain)

    # chainId travels in the body of the submit...
    first = json.loads(route.calls[0].request.content)
    assert first["chainId"] == chain

    # ...and is forwarded as a header on subsequent outbound calls.
    client.agents.run(conn, input={"message": "again"})
    assert route.calls[1].request.headers["X-Praesidia-Chain-Id"] == chain


@respx.mock
def test_forward_chain_at_client_level_then_stop():
    agents_url = f"{BASE_URL}/organizations/{ORG_ID}/agents"
    respx.get(agents_url).mock(return_value=httpx.Response(200, json=[]))

    client = _client()
    client.forward_chain("chain-xyz")
    client.agents.list()
    assert respx.calls.last.request.headers["X-Praesidia-Chain-Id"] == "chain-xyz"

    client.forward_chain(None)
    client.agents.list()
    assert "X-Praesidia-Chain-Id" not in respx.calls.last.request.headers


# ---------------------------------------------------------------------------
# Q4-02 — JIT capability-token forwarding
# ---------------------------------------------------------------------------

POLLED_TASK = {
    "id": "task-9",
    "serverAgentId": "agent-server-7",
    "chainId": "chain-c1",
    "hopIndex": 2,
    "capabilityToken": "jwt.opaque.token",
}


def test_tool_call_headers_from_task_lifts_four_fields():
    headers = tool_call_headers_from_task(POLLED_TASK)
    assert headers == {
        "X-Praesidia-Capability-Token": "jwt.opaque.token",
        "X-Praesidia-Task-Id": "task-9",
        "X-Praesidia-Agent-Id": "agent-server-7",
        "X-Praesidia-Chain-Id": "chain-c1",
    }


def test_tool_call_headers_omits_absent_capability_token():
    headers = tool_call_headers_from_task({"id": "task-1", "chainId": "c-1"})
    assert "X-Praesidia-Capability-Token" not in headers
    assert headers["X-Praesidia-Task-Id"] == "task-1"
    assert headers["X-Praesidia-Chain-Id"] == "c-1"


@respx.mock
def test_poll_pending_tasks_returns_rows_with_chain_and_token():
    poll_url = f"{BASE_URL}/a2a/tasks/pending/client-1"
    respx.get(poll_url).mock(return_value=httpx.Response(200, json=[POLLED_TASK]))

    rows = _client().agents.poll_pending_tasks("client-1")
    assert rows[0]["chainId"] == "chain-c1"
    assert rows[0]["hopIndex"] == 2
    assert rows[0]["capabilityToken"] == "jwt.opaque.token"


@respx.mock
def test_call_mcp_tool_forwards_four_headers_and_keeps_token_out_of_body():
    call_url = (
        f"{BASE_URL}/organizations/{ORG_ID}/mcp-servers/srv-1/tools/search/call"
    )
    route = respx.post(call_url).mock(
        return_value=httpx.Response(200, json={"content": []})
    )

    _client().agents.call_mcp_tool(
        "srv-1",
        "search",
        {"q": "test"},
        task=POLLED_TASK,
    )

    req = route.calls.last.request
    assert req.headers["X-Praesidia-Capability-Token"] == "jwt.opaque.token"
    assert req.headers["X-Praesidia-Task-Id"] == "task-9"
    assert req.headers["X-Praesidia-Agent-Id"] == "agent-server-7"
    assert req.headers["X-Praesidia-Chain-Id"] == "chain-c1"
    # The opaque token must never ride in the JSON body.
    assert b"jwt.opaque.token" not in req.content
    assert json.loads(req.content) == {"arguments": {"q": "test"}}


@respx.mock
def test_call_mcp_tool_explicit_kwargs_override_task():
    call_url = (
        f"{BASE_URL}/organizations/{ORG_ID}/mcp-servers/srv-1/tools/search/call"
    )
    route = respx.post(call_url).mock(
        return_value=httpx.Response(200, json={"content": []})
    )

    _client().agents.call_mcp_tool(
        "srv-1",
        "search",
        {"q": "x"},
        task=POLLED_TASK,
        capability_token="override.token",
    )
    assert (
        route.calls.last.request.headers["X-Praesidia-Capability-Token"]
        == "override.token"
    )


# ---------------------------------------------------------------------------
# Q4-05 — null-secret / JIT-first handling
# ---------------------------------------------------------------------------


@respx.mock
def test_create_returns_jit_mode_with_null_secret():
    agents_url = f"{BASE_URL}/organizations/{ORG_ID}/agents"
    respx.post(agents_url).mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "agent-new",
                "clientId": "ag_public123",
                "clientSecret": None,
                "credentialMode": "jit",
            },
        )
    )
    result = _client().agents.create({"name": "bot"})
    assert result["credentialMode"] == "jit"
    assert result["clientSecret"] is None
    assert result["clientId"] == "ag_public123"
