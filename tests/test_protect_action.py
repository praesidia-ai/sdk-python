"""PA01 DX-002 — tests for AgentsResource.protect_action (parity with sdk's guard.protectAction)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from praesidia import Praesidia
from praesidia.exceptions import (
    ForbiddenError,
    ProtectedActionDeniedError,
    UnsupportedProtectedActionTargetError,
)

BASE_URL = "http://test.local"
ORG_ID = "org-1"
CALL_URL = f"{BASE_URL}/organizations/{ORG_ID}/mcp-servers/srv-1/tools/search/call"


def _client() -> Praesidia:
    return Praesidia(api_key="sk-test", org_id=ORG_ID, base_url=BASE_URL)


def test_unsupported_protocol_raises_before_any_network_call():
    with pytest.raises(UnsupportedProtectedActionTargetError):
        _client().agents.protect_action(
            "srv-1", "search", protocol="http"
        )


@respx.mock
def test_success_returns_dispatch_result_and_sends_correct_body():
    route = respx.post(CALL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "content": [{"type": "text", "text": "ok"}],
                "isError": False,
                "latencyMs": 12,
            },
        )
    )

    result = _client().agents.protect_action(
        "srv-1", "search", {"q": "quarterly filings"}
    )

    assert result["success"] is True
    assert result["content"] == [{"type": "text", "text": "ok"}]
    assert "actionId" not in result or result.get("actionId") is None
    assert json.loads(route.calls.last.request.content) == {
        "toolName": "search",
        "arguments": {"q": "quarterly filings"},
    }


@respx.mock
def test_tool_level_error_does_not_raise():
    respx.post(CALL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "success": False,
                "content": [],
                "isError": True,
                "latencyMs": 5,
                "error": "downstream tool failed",
                "errorCode": "TOOL_ERROR",
            },
        )
    )

    result = _client().agents.protect_action("srv-1", "search")
    assert result["success"] is False
    assert result["isError"] is True


@respx.mock
def test_permit_missing_raises_protected_action_denied_error():
    respx.post(CALL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "success": False,
                "content": [],
                "isError": True,
                "latencyMs": 0,
                "error": 'Tool "search" denied: no Permit presented (X-Praesidia-Permit missing)',
                "errorCode": "PERMIT_MISSING",
            },
        )
    )

    with pytest.raises(ProtectedActionDeniedError) as excinfo:
        _client().agents.protect_action("srv-1", "search")
    assert excinfo.value.error_code == "PERMIT_MISSING"


@respx.mock
def test_confirmed_replay_raises_protected_action_denied_error():
    respx.post(CALL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "success": False,
                "content": [],
                "isError": True,
                "latencyMs": 0,
                "error": "Tool \"search\" denied: Permit already consumed (replay suppressed)",
                "errorCode": "PERMIT_REPLAYED",
            },
        )
    )

    with pytest.raises(ProtectedActionDeniedError) as excinfo:
        _client().agents.protect_action("srv-1", "search")
    assert excinfo.value.error_code == "PERMIT_REPLAYED"


@respx.mock
def test_http_level_denial_propagates_unchanged():
    respx.post(CALL_URL).mock(return_value=httpx.Response(403, text="ABAC denied"))

    with pytest.raises(ForbiddenError):
        _client().agents.protect_action("srv-1", "search")


@respx.mock
def test_permit_header_distinct_from_capability_token_header():
    route = respx.post(CALL_URL).mock(
        return_value=httpx.Response(200, json={"success": True, "content": [], "latencyMs": 1})
    )

    _client().agents.protect_action(
        "srv-1",
        "search",
        permit="permit.jwt.token",
        capability_token="capability.jwt.token",
    )

    headers = route.calls.last.request.headers
    assert headers["X-Praesidia-Permit"] == "permit.jwt.token"
    assert headers["X-Praesidia-Capability-Token"] == "capability.jwt.token"


def test_rejects_invalid_timeout_ms():
    with pytest.raises(ValueError, match="timeout_ms"):
        _client().agents.protect_action("srv-1", "search", timeout_ms=999)
