"""SDK-0301 — interaction hooks (parity with the TS SDK's SDK-0300), replaying be's BE-1486 fixture.

Every behaviour runs against both variants: ``PraesidiaInteractionHooks`` (httpx.Client) and
``AsyncPraesidiaInteractionHooks`` (httpx.AsyncClient).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx

from praesidia import (
    DEFAULT_FAIL_MODES,
    INTERACTION_TYPES,
    INTERACTION_VERDICTS,
    AsyncPraesidiaInteractionHooks,
    AuthError,
    ForbiddenError,
    InteractionDecisionUnavailableError,
    InteractionDeniedError,
    InteractionHookResult,
    PraesidiaConfigError,
    PraesidiaError,
    PraesidiaInteractionHooks,
    interaction_hooks,
)

# Byte-identical (`cmp`) copy of be/test-fixtures/interaction-decision-v1.json; sdk replays the same file.
FIXTURE = json.loads((Path(__file__).parents[1] / "test-fixtures/interaction-decision-v1.json").read_text())
CASES = {c["name"]: c for c in FIXTURE["cases"]}
BASE = "https://api.example"
URL = f"{BASE}/organizations/{FIXTURE['orgId']}/interaction-decisions"
AGENT = CASES["allow_by_policy"]["request"]["agentId"]
ALLOW = CASES["allow_by_policy"]["response"]
DENY = CASES["deny_by_policy"]["response"]
PENDING = CASES["require_approval_minted"]["response"]
CONSUMED = CASES["approval_granted_consumed"]["response"]
MODES = ("sync", "async")


def wire(body):
    """The bytes JS ``JSON.stringify`` produces, i.e. what the TS SDK sends."""
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()


def hooks(mode, **extra):
    cls = PraesidiaInteractionHooks if mode == "sync" else AsyncPraesidiaInteractionHooks
    config = {"api_key": "pk_test", "org_id": FIXTURE["orgId"], "agent_id": AGENT, "base_url": BASE}
    return cls(**{**config, "approval_poll_interval": 0.001, **extra})


def run(value):
    """Resolve a hook call in either mode (an async hook returns an awaitable)."""

    async def main():
        return await value if inspect.isawaitable(value) else value

    return asyncio.run(main())


def seq(*outcomes):
    """respx side effect: each call takes the next outcome (a body or an exception); the last repeats."""
    queue = list(outcomes)

    def respond(request):
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, int):
            return httpx.Response(outcome, text="error")
        return httpx.Response(200, json=outcome)

    return respond


def sent(api, i=0):
    return json.loads(api.calls[i].request.content)


@pytest.fixture
def api():
    with respx.mock(assert_all_called=False) as mock:
        yield mock.post(URL)


# `fail` is the README-documented default, stated here literally, not read from DEFAULT_FAIL_MODES.
HOOKS = [
    ("before_tool_call", "open", "model_to_tool", lambda h: h.before_tool_call("search.web", {"q": "x"})),
    ("before_exec", "closed", "agent_to_shell", lambda h: h.before_exec("rm -rf /srv")),
    ("before_fs_access(read)", "open", "agent_to_filesystem", lambda h: h.before_fs_access("/srv/reports/q3.csv", "read")),
    ("before_fs_access(write)", "closed", "agent_to_filesystem", lambda h: h.before_fs_access("/srv/x", "write")),
    ("before_browser_action", "open", "agent_to_browser", lambda h: h.before_browser_action("navigate", url="https://example.com")),
]
per_hook = pytest.mark.parametrize("name,fail,kind,call", HOOKS, ids=[h[0] for h in HOOKS])
both = pytest.mark.parametrize("mode", MODES)


# ── contract (BE-1486 fixture) ───────────────────────────────────────────────


def test_enums_equal_the_recorded_fixture():
    assert list(INTERACTION_TYPES) == FIXTURE["interactionTypes"]
    assert list(INTERACTION_VERDICTS) == FIXTURE["verdicts"]


@both
@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda c: c["name"])
def test_decide_sends_the_recorded_request_and_returns_the_recorded_response(api, mode, case):
    api.respond(200, json=case["response"])
    req = case["request"]
    assert run(hooks(mode).decide(req["interactionType"], req["action"], req.get("approvalId"))) == case["response"]
    call = api.calls[0].request
    assert (str(call.url), call.method, call.content) == (URL, "POST", wire(req))
    assert call.headers["authorization"] == "Bearer pk_test"


@both
@pytest.mark.parametrize(
    "case,call",
    [
        ("allow_by_policy", lambda h: h.before_fs_access("/srv/reports/q3.csv", "read")),
        ("deny_by_policy", lambda h: h.before_exec("rm -rf /srv")),
        ("deny_no_policy_matched", lambda h: h.before_browser_action("navigate", url="https://example.com")),
    ],
    ids=["fs_access read", "exec", "browser"],
)
def test_hook_body_is_byte_identical_to_the_fixture(api, mode, case, call):
    api.respond(200, json=CASES[case]["response"])
    with contextlib.suppress(InteractionDeniedError):
        run(call(hooks(mode)))
    assert api.calls[0].request.content == wire(CASES[case]["request"])


# ── the four hooks, both variants ────────────────────────────────────────────


@both
@per_hook
def test_allow_passes_through_with_the_decision(api, mode, name, fail, kind, call):
    api.respond(200, json=ALLOW)
    assert run(call(hooks(mode))) == InteractionHookResult(ALLOW)
    assert sent(api)["interactionType"] == kind


@both
@per_hook
def test_deny_raises_interaction_denied_error(api, mode, name, fail, kind, call):
    api.respond(200, json=DENY)
    with pytest.raises(InteractionDeniedError) as err:
        run(call(hooks(mode)))
    assert (err.value.interaction_type, err.value.reason_code, err.value.decision) == (kind, "denied_by_policy", DENY)


@both
@per_hook
def test_require_approval_blocks_until_the_approval_resolves(api, mode, name, fail, kind, call):
    api.mock(side_effect=seq(PENDING, PENDING, PENDING, CONSUMED))
    seen = []
    assert run(call(hooks(mode, on_approval_required=seen.append))) == InteractionHookResult(CONSUMED)
    assert seen == [PENDING] and api.call_count == 4
    assert "approvalId" not in sent(api, 0)
    assert sent(api, 3)["approvalId"] == PENDING["approvalId"]


@both
@per_hook
def test_decision_api_outage_applies_the_documented_fail_mode(api, mode, name, fail, kind, call):
    api.mock(side_effect=httpx.ConnectError("down"))
    if fail == "closed":
        with pytest.raises(InteractionDecisionUnavailableError) as err:
            run(call(hooks(mode)))
        assert isinstance(err.value.__cause__, httpx.ConnectError)
    else:
        result = run(call(hooks(mode)))
        assert result.decision is None and isinstance(result.fail_open_error, httpx.ConnectError)


# ── fail modes ───────────────────────────────────────────────────────────────


def test_defaults_fail_closed_for_exec_and_fs_writes_only():
    assert dict(DEFAULT_FAIL_MODES) == {
        "tool_call": "open", "exec": "closed", "fs_read": "open", "fs_write": "closed", "browser": "open",
    }


@both
@pytest.mark.parametrize(
    "outcome",
    [503, 429, 408, {"verdict": "maybe"}, {**PENDING, "approvalId": None}, {**ALLOW, "ttlSeconds": 1.5},
     httpx.ReadTimeout("slow")],
    ids=["503", "429", "408", "bad verdict", "approval without id", "non-int ttl", "timeout"],
)
def test_fail_closed_exec_treats_as_an_outage(api, mode, outcome):
    api.mock(side_effect=seq(outcome))
    with pytest.raises(InteractionDecisionUnavailableError):
        run(hooks(mode).before_exec("ls"))


@both
@pytest.mark.parametrize("body", [b"not json", b"x" * (64 * 1024 + 1)], ids=["non-JSON", "over 64 KiB"])
def test_unreadable_2xx_is_an_outage(api, mode, body):
    api.respond(200, content=body)
    with pytest.raises(InteractionDecisionUnavailableError):
        run(hooks(mode).before_exec("ls"))


@both
@pytest.mark.parametrize("status,error", [(401, AuthError), (403, ForbiddenError), (400, PraesidiaError)])
def test_a_caller_error_raises_even_on_a_fail_open_hook(api, mode, status, error):
    api.respond(status, text="no")
    with pytest.raises(error) as err:
        run(hooks(mode).before_fs_access("/a", "read"))
    assert err.value.status_code == status


@both
def test_fail_mode_override_flips_a_class(api, mode):
    api.mock(side_effect=httpx.ConnectError("down"))
    assert run(hooks(mode, fail_mode={"exec": "open"}).before_exec("ls")).decision is None
    with pytest.raises(InteractionDecisionUnavailableError):
        run(hooks(mode, fail_mode={"browser": "closed"}).before_browser_action("click"))


@both
def test_before_interaction_defaults_to_fail_closed(api, mode):
    api.mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(InteractionDecisionUnavailableError):
        run(hooks(mode).before_interaction("agent_to_email", {"name": "send"}))
    assert run(hooks(mode).before_interaction("agent_to_email", {"name": "send"}, fail_mode="open")).decision is None


@both
def test_an_outage_while_waiting_for_approval_never_allows_even_fail_open(api, mode):
    api.mock(side_effect=seq(PENDING, httpx.ConnectError("down")))
    with pytest.raises(InteractionDeniedError) as err:
        run(hooks(mode, approval_timeout=0.02).before_browser_action("click"))
    assert (err.value.reason_code, err.value.decision) == ("approval_wait_timeout", PENDING)


@both
def test_a_caller_error_while_waiting_for_approval_raises(api, mode):
    api.mock(side_effect=seq(PENDING, 403))
    with pytest.raises(ForbiddenError):
        run(hooks(mode).before_browser_action("click"))


# ── approval outcomes (fixture) ──────────────────────────────────────────────

EMAIL = CASES["require_approval_minted"]["request"]["action"]


@both
def test_approval_granted_re_post_is_the_recorded_request(api, mode):
    api.mock(side_effect=seq(PENDING, CONSUMED))
    assert run(hooks(mode).before_interaction("agent_to_email", EMAIL)) == InteractionHookResult(CONSUMED)
    assert api.calls[0].request.content == wire(CASES["require_approval_minted"]["request"])
    assert api.calls[1].request.content == wire(CASES["approval_granted_consumed"]["request"])


@both
def test_approval_rejected_raises(api, mode):
    api.mock(side_effect=seq(PENDING, CASES["approval_rejected"]["response"]))
    with pytest.raises(InteractionDeniedError) as err:
        run(hooks(mode).before_interaction("agent_to_email", EMAIL))
    assert err.value.reason_code == "approval_rejected"


# ── report_outcome (BE-1582, parity with the TS SDK's SDK-0324) ──────────────

OUTCOME_URL = f"{URL}/outcome"
RECEIPT = {"approvalId": CONSUMED["approvalId"], "decisionId": "66666666-6666-4666-8666-666666666601"}


@pytest.fixture
def outcome_api():
    with respx.mock(assert_all_called=False) as mock:
        yield mock.post(URL), mock.post(OUTCOME_URL)


@both
def test_consumed_allow_surfaces_approval_id_and_the_report_sends_only_a_commitment(outcome_api, mode):
    decide, report = outcome_api
    decide.mock(side_effect=seq(PENDING, CONSUMED))
    report.mock(return_value=httpx.Response(200, json=RECEIPT))
    h = hooks(mode)
    decision = run(h.before_interaction("agent_to_email", EMAIL)).decision
    assert decision["reasonCode"] == "approval_consumed" and decision["approvalId"] == CONSUMED["approvalId"]
    result = {"messageId": "msg_secret_123", "to": "cfo@example.com"}
    receipt = run(h.report_outcome(decision["approvalId"], "succeeded", result=result, target_system="smtp", target_transaction_id="tx-1"))
    assert receipt == RECEIPT and report.call_count == 1
    body = report.calls[0].request.content
    assert body == wire({
        "agentId": AGENT,
        "approvalId": CONSUMED["approvalId"],
        "status": "succeeded",
        "resultCommitment": interaction_hooks.jcs_commitment(result),
        "targetSystem": "smtp",
        "targetTransactionId": "tx-1",
    })
    assert b"msg_secret_123" not in body and b'"result"' not in body


def test_result_commitment_matches_the_ts_sdk_digest():
    # Expected value computed with the TS SDK's jcsCommitment (core/sdk src/jcs-canonical.ts).
    result = {"to": "cfo@example.com", "messageId": "msg_secret_123", "amount": 12.5}
    assert interaction_hooks.jcs_commitment(result) == "96e14fc6ed52788dfd5257035574f34ed3bd1c4edfe9c7a2feb5065c37acadff"


@both
def test_report_without_result_omits_result_commitment(outcome_api, mode):
    _, report = outcome_api
    report.mock(return_value=httpx.Response(200, json=RECEIPT))
    run(hooks(mode).report_outcome(RECEIPT["approvalId"], "failed_no_effect"))
    assert sent(report) == {"agentId": AGENT, "approvalId": RECEIPT["approvalId"], "status": "failed_no_effect"}


@both
def test_report_409_is_one_typed_error_not_retried(outcome_api, mode):
    _, report = outcome_api
    report.mock(side_effect=[httpx.Response(409, json={"message": "already reported"}), httpx.Response(200, json=RECEIPT)])
    with pytest.raises(PraesidiaError) as err:
        run(hooks(mode).report_outcome(RECEIPT["approvalId"], "succeeded"))
    assert err.value.status_code == 409 and report.call_count == 1


@both
@pytest.mark.parametrize(
    "args,kwargs",
    [((RECEIPT["approvalId"], "done"), {}), (("", "succeeded"), {}), ((RECEIPT["approvalId"], "partial"), {"result": {"n": float("nan")}})],
    ids=["bad status", "no approval_id", "non-JSON result"],
)
def test_report_rejects_bad_input_before_any_request(outcome_api, mode, args, kwargs):
    _, report = outcome_api
    with pytest.raises(PraesidiaConfigError):
        run(hooks(mode).report_outcome(*args, **kwargs))
    assert report.call_count == 0


def test_outcome_statuses_equal_the_be_enum():
    assert interaction_hooks.INTERACTION_OUTCOME_STATUSES == ("succeeded", "failed_no_effect", "partial", "unknown")


# ── decision cache ───────────────────────────────────────────────────────────


def read(h, path="/a"):
    return run(h.before_fs_access(path, "read"))


@both
def test_cache_reuses_a_verdict_for_its_ttl_including_a_deny(api, mode):
    api.respond(200, json=ALLOW)
    h = hooks(mode)
    read(h)
    read(h)
    assert api.call_count == 1
    api.respond(200, json=DENY)
    h2 = hooks(mode)
    for _ in range(2):
        with pytest.raises(InteractionDeniedError):
            read(h2)
    assert api.call_count == 2


def test_a_cached_verdict_expires_after_its_ttl(api, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(interaction_hooks, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=time.sleep))
    api.respond(200, json=ALLOW)
    h = hooks("sync")
    read(h)
    now[0] += ALLOW["ttlSeconds"] + 1
    read(h)
    assert api.call_count == 2


@both
def test_ttl_zero_and_require_approval_are_never_cached(api, mode):
    api.respond(200, json={**ALLOW, "ttlSeconds": 0})
    h = hooks(mode)
    read(h)
    read(h)
    assert api.call_count == 2
    api.mock(side_effect=seq({**PENDING, "ttlSeconds": 30}, CONSUMED))
    seen = []
    h2 = hooks(mode, on_approval_required=seen.append)
    run(h2.before_interaction("agent_to_email", EMAIL))
    run(h2.before_interaction("agent_to_email", EMAIL))
    assert len(seen) == 1


@both
def test_a_new_policy_fingerprint_evicts_every_cached_verdict(api, mode):
    api.mock(side_effect=seq(ALLOW, {**ALLOW, "policyFingerprint": "f2"}, ALLOW))
    h = hooks(mode)
    read(h, "/a")
    read(h, "/b")
    read(h, "/a")
    assert api.call_count == 3


def test_cache_holds_at_most_1000_entries_oldest_evicted_first(api, monkeypatch):
    assert interaction_hooks._MAX_CACHE_ENTRIES == 1000
    monkeypatch.setattr(interaction_hooks, "_MAX_CACHE_ENTRIES", 2)
    api.respond(200, json=ALLOW)
    h = hooks("sync")
    for path in ("/a", "/b", "/c", "/b", "/a"):
        read(h, path)
    assert api.call_count == 4


# ── validation, wrapper, lifecycle ───────────────────────────────────────────


@both
@pytest.mark.parametrize(
    "call",
    [
        lambda h: h.before_tool_call("github/create issue"),
        lambda h: h.before_tool_call("x" * 201),
        lambda h: h.before_tool_call("search", ["not", "an", "object"]),
        lambda h: h.before_tool_call("search", {"n": float("nan")}),
        lambda h: h.before_exec("ls", runtime="docker"),
        lambda h: h.before_interaction("agent_to_fax", {"name": "send"}),
        lambda h: h.before_interaction("agent_to_email", {"name": "send", "arguments": "x"}),
        lambda h: h.before_interaction("agent_to_email", {"name": "send"}, fail_mode="Open"),
        lambda h: h.decide("agent_to_email", {"arguments": {}}),
    ],
    ids=["bad name", "long name", "list args", "NaN arg", "bad runtime", "bad type", "str args", "bad fail_mode", "no name"],
)
def test_invalid_requests_raise_before_any_request(api, mode, call):
    with pytest.raises(PraesidiaConfigError):
        run(call(hooks(mode)))
    assert api.call_count == 0


def test_config_requires_credentials_and_valid_settings(monkeypatch):
    for var in ("PRAESIDIA_API_KEY", "PRAESIDIA_ORG_ID", "PRAESIDIA_AGENT_ID"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(PraesidiaConfigError):
        PraesidiaInteractionHooks(api_key="pk_test", org_id=FIXTURE["orgId"], agent_id="")
    for bad in ({"fail_mode": {"exec": "maybe"}}, {"fail_mode": {"shell": "open"}},
                {"approval_timeout": 0}, {"approval_poll_interval": float("nan")}):
        with pytest.raises(PraesidiaConfigError):
            hooks("sync", **bad)
    monkeypatch.setenv("PRAESIDIA_API_KEY", "pk_env")
    monkeypatch.setenv("PRAESIDIA_ORG_ID", FIXTURE["orgId"])
    monkeypatch.setenv("PRAESIDIA_AGENT_ID", AGENT)
    h = AsyncPraesidiaInteractionHooks()
    assert (h.organization_id, h.agent_id) == (FIXTURE["orgId"], AGENT)


def test_exec_code_runtime_sends_command_args_and_cwd(api):
    api.respond(200, json=ALLOW)
    hooks("sync").before_exec("python", args=("-c", "1"), cwd="/srv", runtime="code")
    assert sent(api) == {
        "interactionType": "agent_to_code_execution",
        "agentId": AGENT,
        "action": {"name": "exec", "arguments": {"command": "python", "args": ["-c", "1"], "cwd": "/srv"}},
    }


def test_guarded_checks_before_every_call_and_keeps_the_signature(api):
    api.mock(side_effect=seq(ALLOW, DENY))
    ran = []

    def search_web(q: str) -> str:
        ran.append(q)
        return f"results for {q}"

    tool = hooks("sync").guarded(search_web)
    assert tool(q="x") == "results for x"
    assert sent(api)["action"] == {"name": "search_web", "arguments": {"q": "x"}}
    with pytest.raises(InteractionDeniedError):
        tool(q="y")
    assert ran == ["x"]
    assert tool.__name__ == "search_web" and inspect.signature(tool) == inspect.signature(search_web)
    with pytest.raises(PraesidiaConfigError):
        hooks("sync").guarded(lambda **kw: None)


def test_async_guarded_wraps_async_and_sync_tools(api):
    api.respond(200, json=ALLOW)
    h = hooks("async")

    async def fetch(url):
        return url.upper()

    def double(x):
        return x * 2

    async def main():
        return await h.guarded(fetch)(url="a"), await h.guarded(double, tool_name="calc.double")(x=2)

    assert asyncio.run(main()) == ("A", 4)
    assert sent(api, 1)["action"]["name"] == "calc.double"


def test_context_managers_close_the_http_client():
    with hooks("sync") as h:
        pass
    assert h._http.client.is_closed

    async def main():
        async with hooks("async") as a:
            pass
        return a

    assert asyncio.run(main())._http.client.is_closed
