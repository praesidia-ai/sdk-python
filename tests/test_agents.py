"""Tests for praesidia.agents client-secret rotation + credential refresh (Q4-01)."""

from __future__ import annotations

import json

import httpx
import respx

from praesidia import Praesidia

BASE_URL = "http://test.local"
ORG_ID = "org-1"
AGENT_ID = "agent-9"
ROTATE = (
    f"{BASE_URL}/organizations/{ORG_ID}/agents/{AGENT_ID}/client-secret/rotate"
)


def _client() -> Praesidia:
    return Praesidia(api_key="sk-test", org_id=ORG_ID, base_url=BASE_URL)


def _rotated(grace_ends_at, grace_seconds, secret="sec_new_9f8e"):
    return {
        "clientId": "ag_1a2b3c4d",
        "clientSecret": secret,
        "graceEndsAt": grace_ends_at,
        "gracePeriodSeconds": grace_seconds,
    }


@respx.mock
def test_rotate_client_secret_with_grace():
    route = respx.post(ROTATE).mock(
        return_value=httpx.Response(
            200, json=_rotated("2026-07-05T12:34:56.000Z", 3600)
        )
    )
    result = _client().agents.rotate_client_secret(
        AGENT_ID, grace_period_seconds=3600
    )

    assert route.called
    assert json.loads(route.calls.last.request.content) == {
        "gracePeriodSeconds": 3600
    }
    assert result["clientId"] == "ag_1a2b3c4d"
    assert result["clientSecret"] == "sec_new_9f8e"
    assert result["graceEndsAt"] == "2026-07-05T12:34:56.000Z"
    assert result["gracePeriodSeconds"] == 3600


@respx.mock
def test_rotate_client_secret_instant_sends_empty_body():
    route = respx.post(ROTATE).mock(
        return_value=httpx.Response(200, json=_rotated(None, 0))
    )
    result = _client().agents.rotate_client_secret(AGENT_ID)

    assert route.called
    assert json.loads(route.calls.last.request.content) == {}
    assert result["graceEndsAt"] is None
    assert result["gracePeriodSeconds"] == 0


@respx.mock
def test_refresh_credential_swaps_auth_header():
    respx.post(ROTATE).mock(
        side_effect=[
            httpx.Response(200, json=_rotated("2026-07-05T12:34:56.000Z", 3600)),
            httpx.Response(200, json=_rotated(None, 0, secret="sec_second")),
        ]
    )
    client = _client()
    rotated = client.agents.rotate_client_secret(
        AGENT_ID, grace_period_seconds=3600
    )

    # Adopt the freshly minted secret in-process, then call again.
    client.agents.refresh_credential(rotated["clientSecret"])
    client.agents.rotate_client_secret(AGENT_ID)

    calls = respx.calls
    assert calls[0].request.headers["X-API-Key"] == "sk-test"
    assert calls[1].request.headers["X-API-Key"] == "sec_new_9f8e"


@respx.mock
def test_client_level_refresh_credential_swaps_auth_header():
    respx.post(ROTATE).mock(
        side_effect=[
            httpx.Response(200, json=_rotated(None, 0)),
            httpx.Response(200, json=_rotated(None, 0)),
        ]
    )
    client = _client()
    client.agents.rotate_client_secret(AGENT_ID)
    client.refresh_credential("sk-rotated")
    client.agents.rotate_client_secret(AGENT_ID)

    calls = respx.calls
    assert calls[0].request.headers["X-API-Key"] == "sk-test"
    assert calls[1].request.headers["X-API-Key"] == "sk-rotated"
