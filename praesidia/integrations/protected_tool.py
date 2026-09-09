"""Managed tools dispatch through Praesidia, never through a supplied local callable.

Framework state is a recovery cursor, not authorization. The backend binds the
checkpoint/request, owns approval and consumes dispatch once. Callers must persist
their framework state and keep it outside model-controlled tool arguments.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, MutableMapping, Optional

from .._jcs_canonical import jcs_canonicalize, jcs_commitment
from ..protected_http import ProtectedHttpResource
from .attempt_store import RuntimeAttempt, RuntimeAttemptStore

RUNTIMES = frozenset({
    "openclaw", "hermes", "zeroclaw", "langgraph", "crewai", "openai-agents",
    "google-adk", "microsoft-agent-framework", "agno", "custom",
})


def _identifier(value: str, label: str, limit: int = 256) -> str:
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > limit or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError(f"{label} must be a nonempty bounded identifier")
    return value


@dataclass(frozen=True)
class RuntimeCall:
    """Actual host/runtime cursor; never populate this from model tool arguments."""

    runtime: str
    thread_id: str
    call_id: str
    task_id: Optional[str] = None

    def checkpoint(self, tool_name: str) -> dict[str, str]:
        if self.runtime not in RUNTIMES:
            raise ValueError("Unsupported checkpoint runtime")
        _identifier(self.thread_id, "thread_id")
        _identifier(self.call_id, "call_id")
        checkpoint = {
            "runtime": self.runtime,
            "threadId": self.thread_id,
            "nodeId": "tool:" + jcs_commitment({"name": tool_name, "callId": self.call_id}),
        }
        if self.task_id is not None:
            from uuid import UUID
            if str(UUID(self.task_id)) != self.task_id:
                raise ValueError("task_id must be a canonical UUID")
            checkpoint["taskId"] = self.task_id
        return checkpoint


@dataclass
class RuntimeBinding:
    """Host-owned persisted framework state and its actual logical call identity.

    ``persist`` optionally commits the framework recovery cursor. Failure aborts
    before resume. A separate mandatory RuntimeAttemptStore claims dispatch
    durably before IO; a native state delta or callback is not a claim store.
    """

    call: RuntimeCall
    state: MutableMapping[str, Any]
    persist: Optional[Callable[[], None]] = None


class ProtectedToolStateError(ValueError):
    """A persisted cursor or server response does not match the intended action."""


class ProtectedToolOutcomeUnknown(RuntimeError):
    """Resume failed after admission; inspect checkpoint, never blindly dispatch again."""

    def __init__(self, approval_id: str) -> None:
        self.approval_id = approval_id
        super().__init__(f"Protected dispatch response unavailable; inspect checkpoint {approval_id}")


class ManagedProtectedTool:
    """An explicit protected HTTP tool, not universal framework interception.

    Only the operator-configured backend target can execute. No local effect
    callable, URL, credential or approval decision is accepted from the model.
    Returned evidence grades are server declarations, not independent verification.
    """

    def __init__(self, resource: ProtectedHttpResource, *, target_id: str,
                 name: str, description: str, attempt_store: RuntimeAttemptStore) -> None:
        if not callable(getattr(attempt_store, "claim", None)):
            raise ValueError("A durable host-owned RuntimeAttemptStore is required")
        self.resource = resource
        self.attempt_store = attempt_store
        self.target_id = _identifier(target_id, "target_id", 128)
        self.name = _identifier(name, "name", 64)
        self.description = _identifier(description, "description", 1000)
        self._lock = RLock()

    @staticmethod
    def _view(value: Any) -> dict[str, Any]:
        if (not isinstance(value, dict)
                or value.get("status") not in {"PENDING", "APPROVED", "REJECTED", "EXPIRED", "CANCELLED"}
                or not all(isinstance(value.get(k), str) and value[k]
                           for k in ("approvalId", "actionId", "requestCommitment"))):
            raise ProtectedToolStateError("Malformed protected checkpoint response")
        if value.get("closure") not in {None, "SUCCEEDED", "FAILED_NO_EFFECT", "PARTIAL",
                "OUTCOME_UNKNOWN", "EVIDENCE_INCOMPLETE", "CANCELLED_BEFORE_DISPATCH", "DENIED",
                "EXPIRED", "TARGET_REJECTED", "REVERSED", "DUPLICATE_SUPPRESSED"}:
            raise ProtectedToolStateError("Malformed protected checkpoint closure")
        if value.get("evidenceGrade") not in {None, "A", "B", "C", "D"}:
            raise ProtectedToolStateError("Malformed protected checkpoint evidence grade")
        return value

    def invoke(self, body: dict[str, Any], binding: RuntimeBinding) -> dict[str, Any]:
        """Prepare/recover, inspect live approval and resume at most once per cursor.

        Re-invocation uses the same native call ID after approval. It never accepts
        an approval Boolean. A lost dispatch response marks state before IO and
        subsequent invocations only read the authoritative checkpoint.
        """
        return self._operate(body, binding, dispatch=True)

    def prepare(self, body: dict[str, Any], binding: RuntimeBinding) -> dict[str, Any]:
        """Prepare/read only, for a framework's native pre-execution approval hook."""
        return self._operate(body, binding, dispatch=False)

    def _operate(self, body: dict[str, Any], binding: RuntimeBinding, *, dispatch: bool) -> dict[str, Any]:
        if not isinstance(binding, RuntimeBinding) or not isinstance(binding.call, RuntimeCall):
            raise ValueError("A host-owned RuntimeBinding is required")
        if not isinstance(body, dict):
            raise ValueError("Protected tool body must be a JSON object")
        # Snapshot before any caller/backend code can mutate the supplied values.
        body = json.loads(jcs_canonicalize(body))
        request = {"targetId": self.target_id, "body": body,
                   "checkpoint": binding.call.checkpoint(self.name)}
        installation_id = getattr(self.resource, "runtime_installation_id", None)
        if isinstance(installation_id, str):
            request["checkpoint"]["installationId"] = installation_id
        fingerprint = jcs_commitment(request)
        key = "praesidia:protected:v1:" + jcs_commitment(request["checkpoint"])

        def save(entry: dict[str, Any]) -> None:
            binding.state[key] = dict(entry)
            if binding.persist:
                binding.persist()

        with self._lock:
            previous = binding.state.get(key)
            if previous is not None and (
                    not isinstance(previous, dict) or previous.get("version") != 1
                    or previous.get("requestFingerprint") != fingerprint
                    or type(previous.get("resumeAttempted")) is not bool):
                raise ProtectedToolStateError("Protected checkpoint request changed")
            # Idempotent prepare also validates the exact server binding if local
            # state was lost or tampered with. It does not execute the target.
            prepared = self._view(self.resource.prepare({**request, "description": self.description}))
            if previous is not None and previous.get("approvalId") != prepared["approvalId"]:
                raise ProtectedToolStateError("Protected checkpoint approval changed")
            entry = {"version": 1, "requestFingerprint": fingerprint,
                     "approvalId": prepared["approvalId"],
                     "resumeAttempted": previous["resumeAttempted"] if previous else False}
            save(entry)
            current = self._view(self.resource.checkpoint(prepared["approvalId"]))
            self._same_action(prepared, current)
            if (current.get("consumedAt") or current.get("closure")
                    or current["status"] != "APPROVED" or entry["resumeAttempted"] or not dispatch):
                return self._result(current, entry["resumeAttempted"])
            try:
                expires = datetime.fromisoformat(current["expiresAt"].replace("Z", "+00:00"))
                if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
                    raise ValueError("Expired approval")
                _identifier(current["approverId"], "approverId")
            except (KeyError, TypeError, AttributeError, ValueError) as exc:
                raise ProtectedToolStateError("Approved checkpoint lacks a live reviewer decision") from exc
            entry["resumeAttempted"] = True
            save(entry)
            claimed = self.attempt_store.claim(RuntimeAttempt(
                prepared["approvalId"], prepared["actionId"], prepared["requestCommitment"]))
            if type(claimed) is not bool:
                raise ProtectedToolStateError("RuntimeAttemptStore must return an actual Boolean claim")
            if not claimed:
                return self._result(current, True)
            try:
                # Resume has a result DTO, not the approval/checkpoint DTO. Its
                # response intentionally has no status, reviewer or expiry.
                result = self.resource.resume({**request, "approvalId": current["approvalId"]})
                if not isinstance(result, dict):
                    raise ProtectedToolStateError("Malformed protected execution result")
                self._same_action(current, result)
                if (result.get("closure") not in {"SUCCEEDED", "FAILED_NO_EFFECT", "PARTIAL", "OUTCOME_UNKNOWN", "EVIDENCE_INCOMPLETE"}
                        or result.get("evidenceGrade") not in {"A", "B", "C", "D"}
                        or "result" not in result or "resultCommitment" not in result
                        or (result["result"] is not None if result["resultCommitment"] is None
                            else jcs_commitment(result["result"]) != result["resultCommitment"])):
                    raise ProtectedToolStateError("Malformed protected execution result")
                observed = self._view(self.resource.checkpoint(current["approvalId"]))
                self._same_action(current, observed)
                if any(observed.get(k) != result.get(k) for k in ("closure", "resultCommitment", "evidenceGrade")):
                    raise ProtectedToolStateError("Execution result differs from its recorded checkpoint")
            except Exception as exc:
                # The effect may have happened. Preserve the durable attempted bit;
                # do not call resume again even if a later read is still pending.
                raise ProtectedToolOutcomeUnknown(current["approvalId"]) from exc
            return self._result(observed, True)

    @staticmethod
    def _same_action(expected: dict[str, Any], actual: dict[str, Any]) -> None:
        if any(expected[k] != actual[k] for k in ("approvalId", "actionId", "requestCommitment")):
            raise ProtectedToolStateError("Checkpoint response belongs to a different action")

    @staticmethod
    def _result(current: dict[str, Any], attempted: bool) -> dict[str, Any]:
        terminal = bool(current.get("closure"))
        disposition = ("recorded_outcome" if terminal else
                       "inspection_required" if attempted or current.get("consumedAt") else
                       "approval_required" if current["status"] == "PENDING" else "not_dispatched")
        return {"kind": "praesidia.protected-tool.v1", "disposition": disposition,
                **{k: current.get(k) for k in (
                    "approvalId", "actionId", "requestCommitment", "status", "consumedAt",
                    "closure", "result", "resultCommitment", "receipt", "evidenceGrade")}}


def decode_body(arguments: str) -> dict[str, Any]:
    """Strict wrapper shape: host context and resume decisions are never tool input."""
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("Duplicate JSON argument key")
            value[key] = item
        return value
    value = json.loads(arguments, object_pairs_hook=unique)
    if not isinstance(value, dict) or set(value) != {"body"} or not isinstance(value["body"], dict):
        raise ValueError("Tool arguments must contain only the body object")
    return value["body"]
