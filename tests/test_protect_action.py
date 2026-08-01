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


# PA-0026 — these are the tests that would have caught the original
# heuristic bug: it decided "pre-dispatch denial?" via
# `errorCode != 'TOOL_ERROR'`, but `'TOOL_ERROR'` is never present in this
# endpoint's caller-visible response at all (it only exists in be's internal
# forensic write). Both cases below satisfy that old heuristic's "raise"
# branch and would have wrongly raised ProtectedActionDeniedError.
@respx.mock
def test_tool_level_error_does_not_raise_no_error_code():
    """A successful call whose tool itself reports failure carries no
    errorCode at all, and no actionDenyReason — verified by tracing
    mcp-client.service.ts's success-with-tool-error return site
    (PA01-FIXED-be3.md "Design decision 2")."""
    respx.post(CALL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "success": False,
                "content": [],
                "isError": True,
                "latencyMs": 5,
                "error": "downstream tool failed",
            },
        )
    )

    result = _client().agents.protect_action("srv-1", "search")
    assert result["success"] is False
    assert result["isError"] is True


@pytest.mark.parametrize("error_code", ["BAD_REQUEST", "INTERNAL_ERROR"])
@respx.mock
def test_downstream_tool_transport_exception_does_not_raise(error_code):
    """A genuine downstream tool/transport exception — NOT a denial — has no
    actionDenyReason even though it carries an errorCode."""
    respx.post(CALL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "success": False,
                "content": [],
                "isError": True,
                "latencyMs": 5,
                "error": "downstream tool threw",
                "errorCode": error_code,
            },
        )
    )

    result = _client().agents.protect_action("srv-1", "search")
    assert result["success"] is False
    assert result["isError"] is True


@pytest.mark.parametrize(
    "action_deny_reason,message",
    [
        ("PERMIT_MISSING", "no Permit presented (X-Praesidia-Permit missing)"),
        ("PERMIT_INVALID", "Permit signature invalid"),
        ("PERMIT_EXPIRED", "Permit expired"),
        ("PERMIT_MISMATCH", "Permit commitment mismatch"),
        ("PERMIT_REPLAYED", "Permit already consumed (replay suppressed)"),
        ("POLICY_DENIED", "denied by agent tool policy"),
    ],
)
@respx.mock
def test_each_deny_reason_raises_protected_action_denied_error(
    action_deny_reason, message
):
    """PA-0026 — actionDenyReason is the reliable discriminator: every one
    of the frozen six values must raise."""
    respx.post(CALL_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "success": False,
                "content": [],
                "isError": True,
                "latencyMs": 0,
                "error": f'Tool "search" denied: {message}',
                "errorCode": f"PERMIT_{action_deny_reason}",
                "actionDenyReason": action_deny_reason,
            },
        )
    )

    with pytest.raises(ProtectedActionDeniedError) as excinfo:
        _client().agents.protect_action("srv-1", "search")
    assert excinfo.value.action_deny_reason == action_deny_reason


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
