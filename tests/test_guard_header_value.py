"""SDK-0359 (twin of TS SDK-0358) -- a header value httpx cannot send raises a config error.

A per-call chain_id with CR/LF/NUL (often end-user derived) or a non-latin-1 char made
httpx raise LocalProtocolError / UnicodeEncodeError while building the request. The
former is an ``httpx.RequestError``, so ``_is_outage`` read it as transport and the Guard
degraded to local-only rules in every failure_mode. Headers are now checked (RFC 9110
token / field-value) before httpx sees them. The SDK's async surface
(``AsyncPraesidiaInteractionHooks``) sends only fixed, constructor-validated headers.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from praesidia._http import HttpClient
from praesidia.exceptions import PraesidiaConfigError
from praesidia.guard import Guard

BASE_URL = "https://test.local"
ORG_ID = "org-uuid-123"
VALIDATE = f"{BASE_URL}/organizations/{ORG_ID}/guardrails/validate"
MODES = ["local_rules", "fail_open", "fail_closed"]
# httpx encodes a str header value as ASCII, so obs-text (latin-1 0x80-0xff) is refused too.
BAD_CHAIN_IDS = ["a\r\nX-Injected: 1", "a\nb", "a\x00b", "café", "a☃b"]


def _guard(mode: str, **kw) -> Guard:
    return Guard(api_key="pk_test_key", org_id=ORG_ID, base_url=BASE_URL, failure_mode=mode, **kw)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("chain_id", BAD_CHAIN_IDS, ids=["crlf-injection", "lf", "nul", "obs-text", "non-latin-1"])
@respx.mock
def test_bad_chain_id_raises_config_error_and_sends_nothing(mode, chain_id):
    route = respx.post(VALIDATE).mock(return_value=httpx.Response(200, json={"passed": True}))
    guard = _guard(mode)
    for check in (guard.check_input, guard.check_output):
        with pytest.raises(PraesidiaConfigError) as exc:
            check("hello", chain_id=chain_id)
        assert chain_id not in str(exc.value)
    with pytest.raises(PraesidiaConfigError):
        guard.run(lambda: "never", input="hello", chain_id=chain_id)
    assert not route.called
    assert guard._degraded_since is None


@respx.mock
def test_legal_non_uuid_chain_id_still_reaches_the_server():
    route = respx.post(VALIDATE).mock(return_value=httpx.Response(200, json={"passed": True}))
    result = _guard("fail_closed").check_input("hello", chain_id="trace-abc\tv1.2")
    assert result["local"] is False and result["passed"] is True
    assert route.calls[0].request.headers["X-Praesidia-Chain-Id"] == "trace-abc\tv1.2"


@pytest.mark.parametrize("mode", ["local_rules", "fail_open"])
@respx.mock
def test_connect_error_still_degrades(mode):
    respx.post(VALIDATE).mock(side_effect=httpx.ConnectError("down"))
    result = _guard(mode, retry=False).check_input("hello", chain_id="trace-abc")
    assert result["degraded"] is True and result["local"] is True


@pytest.mark.parametrize(
    "verb,headers",
    [
        ("post", {"X-Custom": "a\rb"}),
        ("get", {"X-Custom": "☃"}),
        ("get", {"Bad Name": "v"}),
        ("get", {"X-Custom": 1}),
    ],
    ids=["cr-value", "non-latin-1-value", "bad-name", "non-str-value"],
)
@respx.mock
def test_client_rejects_bad_caller_header_on_retry_path_too(verb, headers):
    route = respx.route(url__startswith=BASE_URL).mock(return_value=httpx.Response(200, json={}))
    client = HttpClient(api_key="pk_test_key", org_id=ORG_ID, base_url=BASE_URL)  # default retry policy on
    with pytest.raises(PraesidiaConfigError):
        getattr(client, verb)("/x", **({"json": {}} if verb == "post" else {}), headers=headers)
    assert not route.called
