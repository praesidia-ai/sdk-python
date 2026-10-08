"""SDK-0301 — interaction hooks (parity with the TS SDK's SDK-0300), replaying be's BE-1486 fixture.

Every behaviour runs against both variants: ``PraesidiaInteractionHooks`` (httpx.Client) and
``AsyncPraesidiaInteractionHooks`` (httpx.AsyncClient).
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import re
import time
import typing
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import respx

from praesidia import (
    DEFAULT_FAIL_MODES,
    INTERACTION_CONSTRAINED_BY,
    INTERACTION_TYPES,
    INTERACTION_VERDICTS,
    AsyncPraesidiaInteractionHooks,
    AuthError,
    ForbiddenError,
    IdempotencyKeyReusedError,
    InteractionDecisionRecordDetails,
    InteractionDecisionUnavailableError,
    InteractionDeniedError,
    InteractionHookResult,
    InteractionTaskNotLiveError,
    PraesidiaConfigError,
    PraesidiaError,
    PraesidiaInteractionHooks,
    RateLimitError,
    RetryConfig,
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
NO_WAIT = RetryConfig(base_delay_s=0, max_delay_s=0)  # the default 3 attempts, without sleeping


def wire(body):
    """The bytes JS ``JSON.stringify`` produces, i.e. what the TS SDK sends."""
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()


def hooks(mode, **extra):
    cls = PraesidiaInteractionHooks if mode == "sync" else AsyncPraesidiaInteractionHooks
    config = {"api_key": "pk_test", "org_id": FIXTURE["orgId"], "agent_id": AGENT, "base_url": BASE}
    return cls(**{**config, "approval_poll_interval": 0.001, "retry": NO_WAIT, **extra})


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
    ("before_tool_call", "closed", "model_to_tool", lambda h: h.before_tool_call("search.web", {"q": "x"})),
    ("before_exec", "closed", "agent_to_shell", lambda h: h.before_exec("rm -rf /srv")),
    ("before_fs_access(read)", "closed", "agent_to_filesystem", lambda h: h.before_fs_access("/srv/reports/q3.csv", "read")),
    ("before_fs_access(list)", "closed", "agent_to_filesystem", lambda h: h.before_fs_access("/srv", "list")),
    ("before_fs_access(delete)", "closed", "agent_to_filesystem", lambda h: h.before_fs_access("/srv/x", "delete")),
    ("before_fs_access(write)", "closed", "agent_to_filesystem", lambda h: h.before_fs_access("/srv/x", "write")),
    ("before_browser_action", "closed", "agent_to_browser", lambda h: h.before_browser_action("navigate", url="https://example.com")),
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


@both
@per_hook
@pytest.mark.parametrize("outcome", [503, {"verdict": "maybe"}, b"not json"], ids=["503", "bad verdict", "non-JSON"])
def test_unavailable_or_malformed_decision_blocks_every_hook_by_default(api, mode, name, fail, kind, call, outcome):
    if isinstance(outcome, bytes):
        api.respond(200, content=outcome)
    else:
        api.mock(side_effect=seq(outcome))
    with pytest.raises(InteractionDecisionUnavailableError):
        run(call(hooks(mode)))


@both
@per_hook
def test_explicit_fail_open_override_permits_an_outage_for_each_class(api, mode, name, fail, kind, call):
    api.mock(side_effect=httpx.ConnectError("down"))
    cls = {
        "before_tool_call": "tool_call", "before_exec": "exec",
        "before_fs_access(read)": "fs_read", "before_fs_access(list)": "fs_read",
        "before_fs_access(write)": "fs_write", "before_fs_access(delete)": "fs_write",
        "before_browser_action": "browser",
    }[name]
    result = run(call(hooks(mode, fail_mode={cls: "open"})))
    assert result.decision is None and isinstance(result.fail_open_error, httpx.ConnectError)


# ── fail modes ───────────────────────────────────────────────────────────────


def test_defaults_require_an_authorization_decision_for_every_hook():
    assert dict(DEFAULT_FAIL_MODES) == {
        "tool_call": "closed", "exec": "closed", "fs_read": "closed", "fs_write": "closed", "browser": "closed",
    }


@both
@pytest.mark.parametrize(
    "outcome",
    [503, 408, {"verdict": "maybe"}, {**PENDING, "approvalId": None}, {**ALLOW, "ttlSeconds": 1.5},
     httpx.ReadTimeout("slow")],
    ids=["503", "408", "bad verdict", "approval without id", "non-int ttl", "timeout"],
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
        run(hooks(mode, fail_mode={"fs_read": "open"}).before_fs_access("/a", "read"))
    assert err.value.status_code == status


# SDK-0353 (parity with TS SDK-0352) -- the guard's degrade predicate: a 429 is caller-triggerable
# (shared egress IP), so it must never open a fail-open hook; a 503 still degrades.
@both
@pytest.mark.parametrize("fail", ["open", "closed"])
def test_a_429_raises_on_a_fail_open_and_a_fail_closed_hook(api, mode, fail):
    api.respond(429, text="slow")
    with pytest.raises(RateLimitError) as err:
        run(hooks(mode, fail_mode={"tool_call": fail}).before_tool_call("search.web", {}))
    assert err.value.status_code == 429


@both
def test_a_503_degrades_a_fail_open_hook(api, mode):
    api.respond(503, text="down")
    result = run(hooks(mode, fail_mode={"tool_call": "open"}).before_tool_call("search.web", {}))
    assert result.decision is None and result.fail_open_error.status_code == 503


@both
def test_a_429_while_waiting_for_approval_raises(api, mode):
    api.mock(side_effect=seq(PENDING, 429))
    with pytest.raises(RateLimitError):
        run(hooks(mode, approval_timeout=0.05).before_browser_action("click"))


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
        run(hooks(mode, approval_timeout=0.02, fail_mode={"browser": "open"}).before_browser_action("click"))
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


# BE-1808: a plain ALLOW (approvalId None) reports by its decisionId.
DECISION_RECEIPT = {"approvalId": None, "decisionId": "66666666-6666-4666-8666-666666666602", "reportedDecisionId": ALLOW["decisionId"]}


@both
def test_plain_allow_reports_by_decision_id(outcome_api, mode):
    decide, report = outcome_api
    decide.mock(return_value=httpx.Response(200, json=ALLOW))
    report.mock(return_value=httpx.Response(200, json=DECISION_RECEIPT))
    h = hooks(mode)
    decision = run(h.before_interaction("agent_to_email", EMAIL)).decision
    assert decision["approvalId"] is None
    receipt = run(h.report_outcome(status="succeeded", decision_id=decision["decisionId"], target_system="smtp"))
    assert receipt == DECISION_RECEIPT and report.call_count == 1
    assert report.calls[0].request.content == wire(
        {"agentId": AGENT, "decisionId": ALLOW["decisionId"], "status": "succeeded", "targetSystem": "smtp"}
    )


@both
@pytest.mark.parametrize(
    "args,kwargs",
    [((None, "succeeded"), {}), ((RECEIPT["approvalId"], "succeeded"), {"decision_id": ALLOW["decisionId"]}), ((), {"status": "succeeded", "decision_id": ""})],
    ids=["neither", "both", "empty decision_id"],
)
def test_report_needs_exactly_one_key(outcome_api, mode, args, kwargs):
    _, report = outcome_api
    with pytest.raises(ValueError, match="approval_id|decision_id") as err:
        run(hooks(mode).report_outcome(*args, **kwargs))
    assert isinstance(err.value, PraesidiaConfigError) and report.call_count == 0


@both
@pytest.mark.parametrize("status", [409, 403])
def test_report_by_decision_id_refusal_is_one_typed_error_not_retried(outcome_api, mode, status):
    _, report = outcome_api
    report.mock(side_effect=[httpx.Response(status, json={"message": "refused"}), httpx.Response(200, json=DECISION_RECEIPT)])
    with pytest.raises(PraesidiaError) as err:
        run(hooks(mode).report_outcome(status="succeeded", decision_id=ALLOW["decisionId"]))
    assert err.value.status_code == status and report.call_count == 1


@both
def test_receipt_without_either_echo_is_malformed(outcome_api, mode):
    _, report = outcome_api
    report.mock(return_value=httpx.Response(200, json={"approvalId": None, "decisionId": DECISION_RECEIPT["decisionId"]}))
    with pytest.raises(PraesidiaError, match="malformed"):
        run(hooks(mode).report_outcome(status="unknown", decision_id=ALLOW["decisionId"]))


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


@both
def test_guarded_never_executes_the_tool_without_a_decision_by_default(api, mode):
    api.mock(side_effect=httpx.ConnectError("down"))
    ran = []

    def send_payment(amount):
        ran.append(amount)

    with pytest.raises(InteractionDecisionUnavailableError):
        run(hooks(mode).guarded(send_payment)(amount=42))
    assert ran == []


def test_context_managers_close_the_http_client():
    with hooks("sync") as h:
        pass
    assert h._http.client.is_closed

    async def main():
        async with hooks("async") as a:
            pass
        return a

    assert asyncio.run(main())._http.client.is_closed


# ── SDK-0333 / BE-1609 — taskId on the request, constrainedBy on the response ──

TASK = "33333333-3333-4333-8333-333333333333"


@both
def test_task_id_is_sent_as_camel_case_task_id_last(api, mode):
    api.respond(200, json=DENY)
    req = CASES["deny_by_policy"]["request"]
    with contextlib.suppress(InteractionDeniedError):
        run(hooks(mode, task_id=TASK).before_exec("rm -rf /srv"))
    assert api.calls[0].request.content == wire({**req, "taskId": TASK})


@both
def test_task_id_follows_approval_id_in_decide(api, mode):
    api.respond(200, json=CONSUMED)
    req = CASES["approval_granted_consumed"]["request"]
    run(hooks(mode, task_id=TASK).decide(req["interactionType"], req["action"], req["approvalId"]))
    assert api.calls[0].request.content == wire({**req, "taskId": TASK})


@both
@pytest.mark.parametrize("constrained_by", [None, *INTERACTION_CONSTRAINED_BY])
def test_constrained_by_is_passed_through(api, mode, constrained_by):
    body = {**DENY, "reasonCode": "delegation_chain_unavailable", "constrainedBy": constrained_by}
    api.respond(200, json=body)
    req = CASES["deny_by_policy"]["request"]
    assert run(hooks(mode, task_id=TASK).decide(req["interactionType"], req["action"]))["constrainedBy"] == constrained_by


@pytest.mark.parametrize("bad", ["not-a-uuid", 123, ""])
def test_invalid_task_id_raises_at_construction(bad):
    with pytest.raises(PraesidiaConfigError, match="task_id"):
        hooks("sync", task_id=bad)


# ── Idempotency-Key (SDK-2504, be BE-1759; TS twin SDK-2503) ────────────────

UUID4 = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
REUSED = {"code": "IDEMPOTENCY_KEY_REUSED", "message": "Idempotency-Key was already used with a different request body"}
IN_FLIGHT = {"message": "A request with this Idempotency-Key is already in progress. Retry after it completes."}


def keys(route):
    return [c.request.headers.get("idempotency-key") for c in route.calls]


def report(h):
    return h.report_outcome(RECEIPT["approvalId"], "succeeded")


@both
@pytest.mark.parametrize("first", [httpx.Response(503, text="down"), httpx.Response(429), httpx.ConnectError("reset")],
                         ids=["503", "429", "transport"])
def test_a_retried_call_resends_the_same_key(outcome_api, mode, first):
    decide, outcome = outcome_api
    decide.mock(side_effect=[first, httpx.Response(200, json=ALLOW)])
    outcome.mock(side_effect=[first, httpx.Response(200, json=RECEIPT)])
    h = hooks(mode)
    assert run(h.decide("agent_to_email", EMAIL)) == ALLOW and run(report(h)) == RECEIPT
    for route in (decide, outcome):
        assert route.call_count == 2 and keys(route)[0] == keys(route)[1]
        assert re.fullmatch(UUID4, keys(route)[0])
        assert route.calls[0].request.content == route.calls[1].request.content


@both
def test_every_logical_call_and_every_approval_poll_gets_a_fresh_key(outcome_api, mode):
    decide, outcome = outcome_api
    decide.mock(side_effect=seq(ALLOW, PENDING, CONSUMED))
    outcome.mock(return_value=httpx.Response(200, json=RECEIPT))
    h = hooks(mode)
    run(h.decide("agent_to_email", EMAIL))
    run(h.before_interaction("agent_to_email", EMAIL))  # PENDING, then a poll carrying approvalId
    run(report(h))
    run(report(h))
    sent_keys = keys(decide) + keys(outcome)
    assert len(sent_keys) == 5 and len(set(sent_keys)) == 5
    assert all(re.fullmatch(UUID4, k) for k in sent_keys)


@both
def test_a_caller_supplied_key_is_sent_verbatim(outcome_api, mode):
    decide, outcome = outcome_api
    decide.mock(side_effect=[httpx.Response(502), httpx.Response(200, json=ALLOW)])
    outcome.mock(return_value=httpx.Response(200, json=RECEIPT))
    h = hooks(mode)
    run(h.decide("agent_to_email", EMAIL, idempotency_key="order-42:send"))
    run(h.report_outcome(RECEIPT["approvalId"], "succeeded", idempotency_key="k" * 255))
    assert keys(decide) == ["order-42:send", "order-42:send"] and keys(outcome) == ["k" * 255]


@both
@pytest.mark.parametrize("key", ["k" * 256, "", " padded", "café", "a\nb", 42])
def test_a_bad_caller_key_raises_before_any_request(outcome_api, mode, key):
    decide, outcome = outcome_api
    h = hooks(mode)
    for call in (lambda: h.decide("agent_to_email", EMAIL, idempotency_key=key),
                 lambda: h.report_outcome(RECEIPT["approvalId"], "succeeded", idempotency_key=key)):
        with pytest.raises(PraesidiaConfigError):
            run(call())
    assert decide.call_count == 0 and outcome.call_count == 0


@both
def test_409_idempotency_key_reused_raises_the_typed_error_once(outcome_api, mode):
    decide, outcome = outcome_api
    for route in (decide, outcome):
        route.mock(return_value=httpx.Response(409, json=REUSED))  # every attempt would get it again
    h = hooks(mode, fail_mode={"tool_call": "open"})
    for call in (lambda: h.decide("agent_to_email", EMAIL), lambda: report(h), lambda: h.before_tool_call("search.web")):
        with pytest.raises(IdempotencyKeyReusedError) as err:
            run(call())  # a fail-open hook raises too: a 409 is never an outage
        assert err.value.status_code == 409 and err.value.code == "IDEMPOTENCY_KEY_REUSED" and not err.value.retryable
    assert decide.call_count == 2 and outcome.call_count == 1


@both
def test_a_409_without_a_code_is_the_plain_error_not_retried(outcome_api, mode):
    decide, outcome = outcome_api
    for route in (decide, outcome):
        route.mock(return_value=httpx.Response(409, json=IN_FLIGHT))
    h = hooks(mode)
    for call in (lambda: h.decide("agent_to_email", EMAIL), lambda: report(h)):
        with pytest.raises(PraesidiaError) as err:
            run(call())
        assert err.value.status_code == 409 and not isinstance(err.value, IdempotencyKeyReusedError)
    assert decide.call_count == 1 and outcome.call_count == 1


@both
def test_retry_false_sends_once(api, mode):
    api.respond(503, text="down")
    result = run(hooks(mode, retry=False, fail_mode={"tool_call": "open"}).before_tool_call("search.web"))
    assert result.fail_open_error.status_code == 503 and api.call_count == 1


def test_an_invalid_retry_config_is_a_config_error():
    with pytest.raises(PraesidiaConfigError):
        hooks("sync", retry=RetryConfig(max_attempts=0))


# ── SDK-2800 / be BE-2836 — a taskId that is not a live task is a typed 403 ──

# be 5bdee08a's exact 403 body: interaction-decisions.service.ts's ForbiddenException run through
# be's global AllExceptionsFilter (no ``code``, no ``error``); only timestamp/requestId vary.
NOT_LIVE = {
    "statusCode": 403,
    "timestamp": "2026-10-01T12:30:10.811Z",
    "path": f"/organizations/{FIXTURE['orgId']}/interaction-decisions",
    "method": "POST",
    "requestId": "00000000-0000-4000-8000-0000000000ff",
    "message": "taskId is not a live task this agent executes",
}


@both
def test_a_stale_task_id_403_is_interaction_task_not_live_error_once(api, mode):
    api.side_effect = [httpx.Response(403, json=NOT_LIVE), httpx.Response(200, json=ALLOW)]
    req = CASES["allow_by_policy"]["request"]
    with pytest.raises(InteractionTaskNotLiveError) as err:
        run(hooks(mode, task_id=TASK).decide(req["interactionType"], req["action"]))
    assert isinstance(err.value, ForbiddenError)
    assert err.value.status_code == 403 and err.value.task_id == TASK and not err.value.retryable
    assert err.value.request_id == NOT_LIVE["requestId"] and api.call_count == 1


@both
def test_a_fail_open_hook_raises_it_never_fail_opens(api, mode):
    api.respond(403, json=NOT_LIVE)
    with pytest.raises(InteractionTaskNotLiveError):
        run(hooks(mode, task_id=TASK, fail_mode={"tool_call": "open"}).before_tool_call("search.web"))


@both
def test_any_other_403_stays_the_plain_forbidden_error(api, mode):
    api.respond(403, json={**NOT_LIVE, "message": "Caller may not act as this agent"})
    with pytest.raises(ForbiddenError) as err:
        run(hooks(mode, task_id=TASK, fail_mode={"tool_call": "open"}).before_tool_call("search.web"))
    assert not isinstance(err.value, InteractionTaskNotLiveError)


def test_interaction_decision_record_details_types_the_be_2836_keys():
    hints = typing.get_type_hints(InteractionDecisionRecordDetails)
    assert hints == {
        "delegationReason": typing.Literal["delegation_implicit_live_task"],
        "constrainingTaskId": str | None,
        "delegationBypass": typing.Literal["owner"],
    }
    assert InteractionDecisionRecordDetails.__required_keys__ == frozenset()
