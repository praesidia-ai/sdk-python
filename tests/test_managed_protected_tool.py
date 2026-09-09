"""Client recovery invariants. The platform double is not backend auth evidence."""
import json
from uuid import UUID, uuid5, NAMESPACE_URL
from concurrent.futures import ThreadPoolExecutor

import pytest

from praesidia.integrations.protected_tool import (
    ManagedProtectedTool, RuntimeBinding, RuntimeCall, ProtectedToolStateError,
    ProtectedToolOutcomeUnknown, decode_body,
)
from praesidia.integrations import FileRuntimeAttemptStore
from praesidia._jcs_canonical import jcs_commitment


class Platform:
    def __init__(self):
        self.requests = {}
        self.dispatches = 0
        self.status = "PENDING"
        self.closure = None
        self.lost = False
        self.view_override = {}

    def prepare(self, request):
        key = jcs_commitment(request["checkpoint"])
        binding = jcs_commitment({k: request[k] for k in ("targetId", "body", "checkpoint")})
        if key in self.requests and self.requests[key] != binding:
            raise PermissionError("server checkpoint request differs")
        self.requests[key] = binding
        self.current_key = key
        return self.checkpoint(str(uuid5(NAMESPACE_URL, "approval-" + key)))

    def checkpoint(self, approval_id):
        return {"approvalId": str(uuid5(NAMESPACE_URL, "approval-" + self.current_key)),
                "actionId": str(uuid5(NAMESPACE_URL, "action-" + self.current_key)),
                "requestCommitment": self.requests[self.current_key],
                "status": self.status, "approverId": "independent-reviewer",
                "expiresAt": "2099-01-01T00:00:00.000Z",
                "consumedAt": "2026-09-06T00:00:00Z" if self.closure else None,
                "closure": self.closure, "result": {"effect": 1} if self.closure else None,
                "evidenceGrade": "C", "resultCommitment": jcs_commitment({"effect": 1}) if self.closure else None, **self.view_override}

    def resume(self, request):
        if self.status != "APPROVED" or self.closure:
            raise PermissionError("server rejected dispatch")
        self.dispatches += 1
        self.closure = "OUTCOME_UNKNOWN" if self.lost else "SUCCEEDED"
        if self.lost:
            raise TimeoutError("response lost after target effect")
        view = self.checkpoint(request["approvalId"])
        return {k: view[k] for k in ("approvalId", "actionId", "requestCommitment", "closure",
                                     "result", "resultCommitment", "evidenceGrade")}


@pytest.fixture
def platform(tmp_path):
    result = Platform()
    result.attempt_directory = tmp_path / "attempts"
    return result


def managed(platform):
    return ManagedProtectedTool(platform, target_id="configured-target", name="write_record", description="Write one approved record",
                                attempt_store=FileRuntimeAttemptStore(platform.attempt_directory))


def binding(state=None, **kwargs):
    return RuntimeBinding(RuntimeCall("custom", "actual-session", "native-call", **kwargs), state if state is not None else {})


def test_pending_restart_approval_one_effect_and_live_readback(platform):
    cursor = binding()
    first = managed(platform).invoke({"amount": 5}, cursor)
    assert first["disposition"] == "approval_required"
    assert platform.dispatches == 0
    restored = binding(json.loads(json.dumps(cursor.state)))
    platform.status = "APPROVED"
    result = managed(platform).invoke({"amount": 5}, restored)
    assert result["closure"] == "SUCCEEDED"
    assert platform.dispatches == 1
    assert managed(platform).invoke({"amount": 5}, restored)["closure"] == "SUCCEEDED"
    assert platform.dispatches == 1


def test_missing_local_state_reprepares_same_authoritative_checkpoint(platform):
    managed(platform).invoke({"amount": 5}, binding())
    platform.status = "APPROVED"
    assert managed(platform).invoke({"amount": 5}, binding())["closure"] == "SUCCEEDED"
    assert managed(platform).invoke({"amount": 5}, binding())["closure"] == "SUCCEEDED"
    assert len(platform.requests) == platform.dispatches == 1


@pytest.mark.parametrize("state_lost", [False, True])
def test_changed_args_cannot_reuse_approval_even_after_local_state_loss(platform, state_lost):
    cursor = binding()
    managed(platform).invoke({"amount": 5}, cursor)
    platform.status = "APPROVED"
    with pytest.raises((ProtectedToolStateError, PermissionError)):
        managed(platform).invoke({"amount": 999}, binding() if state_lost else cursor)
    assert platform.dispatches == 0


@pytest.mark.parametrize("status", ["PENDING", "REJECTED", "CANCELLED", "EXPIRED"])
def test_framework_resume_flags_never_authorize(platform, status):
    cursor = binding({"approved": True, "resume": True, "approverId": "attacker"})
    platform.status = status
    result = managed(platform).invoke({}, cursor)
    assert result["status"] == status
    assert result["disposition"] in {"approval_required", "not_dispatched"}
    assert platform.dispatches == 0


@pytest.mark.parametrize("fields", [
    {"approverId": None}, {"approverId": ""}, {"expiresAt": None},
    {"expiresAt": "garbage"}, {"expiresAt": "2000-01-01T00:00:00Z"},
    {"expiresAt": "2099-01-01T00:00:00"},
])
def test_approved_without_current_reviewer_expiry_fails_closed(platform, fields):
    platform.status = "APPROVED"
    platform.view_override = fields
    with pytest.raises(ProtectedToolStateError, match="reviewer"):
        managed(platform).invoke({}, binding())
    assert platform.dispatches == 0


@pytest.mark.parametrize("fields", [{"status": "allowed"}, {"status": True}, {"approvalId": None}, {"actionId": ""}, {"requestCommitment": None}])
def test_malformed_server_decision_fails_closed(platform, fields):
    platform.view_override = fields
    with pytest.raises(ProtectedToolStateError):
        managed(platform).invoke({}, binding())
    assert platform.dispatches == 0


def test_readback_cannot_switch_the_action(platform):
    original = platform.prepare
    def prepare(req):
        result = original(req)
        platform.view_override = {"actionId": "different"}
        return result
    platform.prepare = prepare
    with pytest.raises(ProtectedToolStateError, match="different action"):
        managed(platform).invoke({}, binding())


def test_persist_failure_prevents_dispatch(platform):
    platform.status = "APPROVED"
    cursor = binding()
    def persist():
        if next(iter(cursor.state.values()))["resumeAttempted"]:
            raise OSError("durable state write failed")
    cursor.persist = persist
    with pytest.raises(OSError):
        managed(platform).invoke({}, cursor)
    assert platform.dispatches == 0


def test_lost_response_and_restart_never_redispatch(platform):
    platform.status = "APPROVED"
    platform.lost = True
    cursor = binding()
    with pytest.raises(ProtectedToolOutcomeUnknown) as error:
        managed(platform).invoke({}, cursor)
    assert str(UUID(error.value.approval_id)) == error.value.approval_id
    restored = binding(json.loads(json.dumps(cursor.state)))
    assert managed(platform).invoke({}, restored)["closure"] == "OUTCOME_UNKNOWN"
    assert platform.dispatches == 1
    # Even a temporarily unconsumed view is inspection-only after an attempted IO.
    platform.closure = None
    assert managed(platform).invoke({}, restored)["disposition"] == "inspection_required"
    assert platform.dispatches == 1


@pytest.mark.parametrize("closure", ["SUCCEEDED", "FAILED_NO_EFFECT", "PARTIAL", "OUTCOME_UNKNOWN", "EVIDENCE_INCOMPLETE", "CANCELLED_BEFORE_DISPATCH"])
def test_existing_outcome_is_preserved_without_synthesizing_success(platform, closure):
    platform.closure = closure
    assert managed(platform).invoke({}, binding())["closure"] == closure
    assert platform.dispatches == 0


def test_two_concurrent_calls_share_cursor_and_single_dispatch(platform):
    platform.status = "APPROVED"
    tool, cursor = managed(platform), binding()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: tool.invoke({}, cursor), range(2)))
    assert all(r["closure"] == "SUCCEEDED" for r in results)
    assert platform.dispatches == 1


def test_prepare_for_native_approval_hook_never_executes(platform):
    platform.status = "APPROVED"
    assert managed(platform).prepare({}, binding())["disposition"] == "not_dispatched"
    assert platform.dispatches == 0


@pytest.mark.parametrize("field,value", [("runtime", "invented"), ("thread_id", ""), ("call_id", None), ("call_id", "x\n"), ("task_id", "not-a-uuid")])
def test_invalid_runtime_identity_fails_before_platform(platform, field, value):
    values = dict(runtime="custom", thread_id="session", call_id="call")
    values[field] = value
    with pytest.raises((TypeError, ValueError)):
        managed(platform).invoke({}, RuntimeBinding(RuntimeCall(**values), {}))
    assert platform.requests == {}


@pytest.mark.parametrize("raw", ['{"body":{},"approved":true}', '{"body":{},"body":{}}', '{"body":{"x":1,"x":2}}', '{"body":[]}', '[]'])
def test_model_cannot_supply_authority_or_ambiguous_arguments(raw):
    with pytest.raises(ValueError):
        decode_body(raw)


def test_json_roundtrip_and_task_binding_are_exact(platform):
    cursor = binding(task_id="00000000-0000-4000-8000-000000000001")
    result = managed(platform).invoke(decode_body('{"body":{"amount":5,"optional":null}}'), cursor)
    assert result["disposition"] == "approval_required"
    assert cursor.call.checkpoint("write_record")["taskId"] == "00000000-0000-4000-8000-000000000001"


def test_tampered_local_approval_is_not_accepted(platform):
    cursor = binding()
    managed(platform).invoke({}, cursor)
    entry = next(iter(cursor.state.values()))
    entry["approvalId"] = "other-approval"
    with pytest.raises(ProtectedToolStateError, match="approval changed"):
        managed(platform).invoke({}, cursor)
    assert platform.dispatches == 0


@pytest.mark.parametrize("field,value", [("closure", "SUCCESS"), ("evidenceGrade", "verified"),
                                         ("status", None), ("consumedAt", None)])
def test_malformed_views_or_consumed_without_closure_never_imply_success(platform, field, value):
    platform.status = "APPROVED"
    if field == "consumedAt":
        platform.view_override = {"consumedAt": "2026-09-06T00:00:00Z", "closure": None}
        assert managed(platform).invoke({}, binding())["disposition"] == "inspection_required"
    else:
        platform.view_override = {field: value}
        with pytest.raises(ProtectedToolStateError):
            managed(platform).invoke({}, binding())
    assert platform.dispatches == 0


@pytest.mark.parametrize("closure", ["DENIED", "EXPIRED", "TARGET_REJECTED", "REVERSED", "DUPLICATE_SUPPRESSED"])
def test_all_server_terminal_closures_are_preserved_without_dispatch(platform, closure):
    platform.closure = closure
    result = managed(platform).invoke({}, binding())
    assert result["closure"] == closure
    assert result["disposition"] == "recorded_outcome"
    assert platform.dispatches == 0


def test_actual_resume_dto_has_no_approval_fields_and_reads_consumed_checkpoint(platform):
    platform.status = "APPROVED"
    result = managed(platform).invoke({}, binding())
    assert result["status"] == "APPROVED"
    assert result["closure"] == "SUCCEEDED"
    assert result["consumedAt"]
    assert platform.dispatches == 1


def test_revoked_checkpoint_retains_real_grade_d_and_cancellation(platform):
    platform.status = "CANCELLED"
    platform.view_override = {"closure": "CANCELLED_BEFORE_DISPATCH", "evidenceGrade": "D", "consumedAt": None}
    result = managed(platform).invoke({}, binding())
    assert result["closure"] == "CANCELLED_BEFORE_DISPATCH" and result["evidenceGrade"] == "D"
    assert platform.dispatches == 0


@pytest.mark.parametrize("override", [None, {"resultCommitment": "wrong"}, {"closure": "SUCCESS"},
                                       {"result": {"changed": 1}}, {"evidenceGrade": "verified"},
                                       {"resultCommitment": None}])
def test_bad_result_dto_is_unknown_and_never_retried(platform, override):
    platform.status = "APPROVED"
    real = platform.resume
    def result(request):
        value = real(request)
        return None if override is None else {**value, **override}
    platform.resume = result
    cursor = binding()
    with pytest.raises(ProtectedToolOutcomeUnknown):
        managed(platform).invoke({}, cursor)
    assert managed(platform).invoke({}, cursor)["closure"] == "SUCCEEDED"
    assert platform.dispatches == 1


def test_fresh_adapter_and_lost_framework_state_cannot_repeat_ambiguous_io(platform):
    platform.status = "APPROVED"
    calls = []
    def resume(request):
        calls.append(request)
        raise TimeoutError("request may still be in flight; consumption is not yet observed")
    platform.resume = resume
    cursor = binding()
    cursor.persist = lambda: None  # A callback return does not establish durability.
    with pytest.raises(ProtectedToolOutcomeUnknown):
        managed(platform).invoke({}, cursor)
    # New instance, new file store and completely missing native state.
    assert managed(platform).invoke({}, binding())["disposition"] == "inspection_required"
    assert len(calls) == 1 and platform.dispatches == 0


def test_missing_invalid_and_failed_attempt_store_never_dispatch(platform):
    platform.status = "APPROVED"
    options = dict(target_id="configured-target", name="write_record", description="Write one approved record")
    with pytest.raises(TypeError):
        ManagedProtectedTool(platform, **options)
    with pytest.raises(ValueError, match="RuntimeAttemptStore"):
        ManagedProtectedTool(platform, attempt_store=None, **options)
    class BadStore:
        def claim(self, attempt):
            return "yes"
    with pytest.raises(ProtectedToolStateError, match="Boolean"):
        ManagedProtectedTool(platform, attempt_store=BadStore(), **options).invoke({}, binding())
    class FailedStore:
        def claim(self, attempt):
            raise OSError("claim storage unavailable")
    with pytest.raises(OSError, match="claim storage"):
        ManagedProtectedTool(platform, attempt_store=FailedStore(), **options).invoke({}, binding())
    assert platform.dispatches == 0
