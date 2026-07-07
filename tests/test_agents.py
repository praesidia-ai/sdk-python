"""Tests for praesidia.agents runtime credential refresh (Q4-01)."""

from __future__ import annotations

import httpx
import respx

from praesidia import Praesidia

BASE_URL = "http://test.local"
ORG_ID = "org-1"
AGENT_ID = "agent-9"
GET_AGENT = f"{BASE_URL}/organizations/{ORG_ID}/agents/{AGENT_ID}"


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
