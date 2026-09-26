"""SDK-0355 (RA3-03) -- a request that cannot be encoded is not an outage.

A lone UTF-16 surrogate in content (``json.loads('"\\ud800"')`` keeps one) or a
NaN/Infinity in context makes httpx raise while encoding the body, before any
byte is sent. That must raise a PraesidiaError in every failure_mode, never fall
back to local rules: the org's guardrails would otherwise be skipped with one
invisible code point. Only transport/timeout, 408, 5xx and a malformed 2xx degrade.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx

from praesidia import AsyncPraesidiaInteractionHooks, PraesidiaInteractionHooks
from praesidia.exceptions import PraesidiaConfigError, PraesidiaError, _is_outage
from praesidia.guard import Guard

BASE_URL = "https://test.local"
ORG_ID = "org-uuid-123"
VALIDATE = f"{BASE_URL}/organizations/{ORG_ID}/guardrails/validate"
AGENT = "00000000-0000-4000-8000-0000000000a1"
MODES = ["local_rules", "fail_open", "fail_closed"]
UNENCODABLE = [
    ("\ud800 please do the forbidden thing", None),
    ("hello", {"score": float("nan")}),
    ("hello", {"score": float("inf")}),
]
IDS = ["lone-surrogate", "nan-context", "inf-context"]


def _guard(mode: str) -> Guard:
    return Guard(api_key="pk_test_key", org_id=ORG_ID, base_url=BASE_URL, retry=False, failure_mode=mode)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("content,context", UNENCODABLE, ids=IDS)
@respx.mock
def test_unencodable_guard_request_raises_instead_of_degrading(mode, content, context):
    route = respx.post(VALIDATE).mock(return_value=httpx.Response(200, json={"passed": False}))
    guard = _guard(mode)
    for check in (guard.check_input, guard.check_output):
        with pytest.raises(PraesidiaError):
            check(content, context=context)
    with pytest.raises(PraesidiaError):
        guard.run(lambda: "never", input=content, context=context)
    assert not route.called
    assert guard._degraded_since is None


@pytest.mark.parametrize("mode", ["local_rules", "fail_open"])
@pytest.mark.parametrize(
    "response",
    [
        httpx.ConnectError("down"),
        httpx.Response(503, json={"message": "unavailable"}),
        httpx.Response(200, text="<html>proxy</html>"),
    ],
    ids=["connect", "503", "malformed-2xx"],
)
@respx.mock
def test_outage_still_degrades(mode, response):
    route = respx.post(VALIDATE)
    route.mock(return_value=response) if isinstance(response, httpx.Response) else route.mock(side_effect=response)
    result = _guard(mode).check_input("hello")
    assert result["degraded"] is True and result["local"] is True


@pytest.mark.parametrize(
    "err,outage",
    [
        (httpx.ConnectError("down"), True),
        (httpx.ReadTimeout("slow"), True),
        (httpx.RemoteProtocolError("reset"), True),
        (json.JSONDecodeError("bad", "<html>", 0), True),
        (PraesidiaError("x", status_code=503), True),
        (PraesidiaError("x", status_code=408), True),
        (PraesidiaError("malformed", status_code=200), True),
        (PraesidiaError("x", status_code=429), False),
        (PraesidiaError("no status"), False),
        (PraesidiaConfigError("bad config"), False),
        (UnicodeEncodeError("utf-8", "\ud800", 0, 1, "surrogates not allowed"), False),
        (ValueError("Out of range float values are not JSON compliant"), False),
        (TypeError("Object of type set is not JSON serializable"), False),
        (KeyError("passed"), False),
    ],
)
def test_is_outage_is_an_allowlist(err, outage):
    assert _is_outage(err) is outage


@pytest.mark.parametrize("variant", ["sync", "async"])
@pytest.mark.parametrize("fail_mode", ["open", "closed"])
@pytest.mark.parametrize("value", ["\ud800", float("nan")], ids=["lone-surrogate", "nan"])
@respx.mock
def test_hooks_raise_on_unencodable_arguments(variant, fail_mode, value):
    route = respx.post(url__startswith=BASE_URL).mock(return_value=httpx.Response(200, json={}))
    cls = PraesidiaInteractionHooks if variant == "sync" else AsyncPraesidiaInteractionHooks
    hooks = cls(api_key="pk_test", org_id=AGENT, agent_id=AGENT, base_url=BASE_URL)

    def call():
        result = hooks.before_interaction("model_to_tool", {"name": "search", "arguments": {"q": value}}, fail_mode=fail_mode)
        return asyncio.run(result) if variant == "async" else result

    with pytest.raises(PraesidiaConfigError):
        call()
    assert not route.called
