"""SDK-0336 -- parity with SDK-0335 (`sdk/src/guard-failure-mode.spec.ts`).

Bounded degradation when the control plane is unreachable: ``failure_mode``
(with the legacy ``strict``/``fail_open`` mapping), ``max_degraded_ms``,
``on_degraded``, and ``degraded=True`` on locally-served results.
"""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

import praesidia.guard as guard_mod
from praesidia.exceptions import PraesidiaConfigError, ServerError
from praesidia.guard import Guard

BASE_URL = "http://test.local"
ORG_ID = "org-uuid-123"
VALIDATE = f"{BASE_URL}/organizations/{ORG_ID}/guardrails/validate"
DOWN = httpx.Response(503, json={"message": "unavailable"})
UP = httpx.Response(200, json={"passed": True, "triggered": [], "processingTimeMs": 1})


def _guard(**kw):
    return Guard(api_key="pk_test_key", org_id=ORG_ID, base_url=BASE_URL, retry=False, **kw)


def _levels(caplog, level):
    return [r for r in caplog.records if r.name == "praesidia.guard" and r.levelno == level]


@pytest.fixture
def clock(monkeypatch):
    now = {"ms": 1_000_000}
    monkeypatch.setattr(guard_mod, "_now_ms", lambda: now["ms"])
    return now


class TestLegacyFlagMapping:
    @pytest.mark.parametrize(
        "flags,mode",
        [
            ({}, "local_rules"),
            ({"strict": True}, "fail_closed"),
            ({"fail_open": True}, "fail_open"),
            ({"strict": True, "fail_open": True}, "fail_open"),
        ],
    )
    @respx.mock
    def test_maps(self, flags, mode, caplog):
        caplog.set_level(logging.WARNING, logger="praesidia.guard")
        respx.post(VALIDATE).mock(return_value=DOWN)
        seen = []
        guard = _guard(on_degraded=seen.append, **flags)
        assert guard.failure_mode == mode
        if mode == "fail_closed":
            with pytest.raises(ServerError):
                guard.check_input("hello")
        else:
            r = guard.check_input("hello")
            assert r["local"] is True and r["degraded"] is True and r["passed"] is True
        assert seen[0]["mode"] == mode and seen[0]["operation"] == "guardrails/validate"
        # local_rules warns; fail_open stays silent (legacy fail_open behaviour).
        assert len(_levels(caplog, logging.WARNING)) == (1 if mode == "local_rules" else 0)

    @respx.mock
    def test_explicit_failure_mode_wins_over_legacy_flags(self):
        respx.post(VALIDATE).mock(return_value=DOWN)
        guard = _guard(fail_open=True, failure_mode="fail_closed")
        with pytest.raises(ServerError):
            guard.check_input("hello")

    def test_rejects_unknown_failure_mode_and_invalid_max_degraded_ms(self):
        with pytest.raises(PraesidiaConfigError):
            _guard(failure_mode="open")
        with pytest.raises(PraesidiaConfigError):
            _guard(max_degraded_ms=-1)
        with pytest.raises(PraesidiaConfigError):
            _guard(max_degraded_ms=float("inf"))


@respx.mock
def test_fail_closed_raises_on_network_error():
    respx.post(VALIDATE).mock(return_value=DOWN)
    with pytest.raises(ServerError):
        _guard(failure_mode="fail_closed").check_output("x")


@respx.mock
def test_local_rules_marks_degraded_and_healthy_result_is_not():
    respx.post(VALIDATE).mock(side_effect=[DOWN, UP])
    guard = _guard(failure_mode="local_rules")
    r = guard.check_input("hi")
    assert r["local"] is True and r["degraded"] is True
    ok = guard.check_input("hi")
    assert ok["local"] is False and "degraded" not in ok


def test_offline_mode_is_not_degraded():
    r = Guard(api_key=None, org_id=None).check_input("hi")
    assert r["local"] is True and "degraded" not in r


@pytest.mark.parametrize("mode", ["local_rules", "fail_open"])
@respx.mock
def test_max_degraded_ms_escalates_until_one_success(mode, clock, caplog):
    caplog.set_level(logging.WARNING, logger="praesidia.guard")
    respx.post(VALIDATE).mock(side_effect=[DOWN, DOWN, DOWN, UP, DOWN])
    guard = _guard(failure_mode=mode, max_degraded_ms=60_000)
    assert guard.check_input("a")["degraded"] is True
    clock["ms"] = 1_000_000 + 60_000  # at the bound: still degraded
    assert guard.check_input("a")["degraded"] is True
    clock["ms"] = 1_000_000 + 60_001  # past the bound: fail closed
    with pytest.raises(ServerError):
        guard.check_input("a")
    assert len(_levels(caplog, logging.ERROR)) == 1  # loud escalation signal
    assert guard.check_input("a")["local"] is False  # success resets
    clock["ms"] = 1_000_000 + 200_000  # new episode starts its own clock
    assert guard.check_input("a")["degraded"] is True


@respx.mock
def test_on_degraded_fires_once_per_episode_and_resets_after_success(clock):
    clock["ms"] = 5_000
    respx.post(VALIDATE).mock(side_effect=[DOWN, DOWN, UP, DOWN])
    seen = []
    guard = _guard(on_degraded=seen.append)
    guard.check_input("a")
    clock["ms"] = 6_000
    guard.check_input("a")
    assert seen == [{"operation": "guardrails/validate", "since": 5_000, "mode": "local_rules"}]
    guard.check_input("a")  # success ends the episode
    clock["ms"] = 9_000
    guard.check_input("a")
    assert len(seen) == 2 and seen[-1]["since"] == 9_000


@respx.mock
def test_on_degraded_fires_in_fail_closed_too():
    respx.post(VALIDATE).mock(return_value=DOWN)
    seen = []
    guard = _guard(failure_mode="fail_closed", on_degraded=seen.append)
    with pytest.raises(ServerError):
        guard.check_input("a")
    assert len(seen) == 1 and seen[0]["mode"] == "fail_closed"


@respx.mock
def test_throwing_on_degraded_does_not_break_the_degraded_path():
    respx.post(VALIDATE).mock(return_value=DOWN)

    def boom(_info):
        raise RuntimeError("pager down")

    assert _guard(on_degraded=boom).check_input("a")["degraded"] is True
