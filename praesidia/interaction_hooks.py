"""
SDK-0301 — interaction hooks: an ADVISORY IN-RUNTIME GUARD (parity with the TS SDK's SDK-0300).

Before your agent runs a shell command, touches a file, drives a browser or calls a tool, a hook
asks Praesidia (``POST /organizations/{orgId}/interaction-decisions``, be BE-1486) and honours the
verdict in your process. Praesidia does not run or intercept that runtime: an agent that does not
load the SDK, or skips a hook, is not governed by it.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
import math
import os
import re
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import (
    Any,
    Generic,
    Literal,
    TypedDict,
    TypeVar,
)

import httpx

from ._http import (
    _DEFAULT_TIMEOUT,
    HttpClient,
    _validate_api_key,
    _validate_idempotency_key,
    _validate_timeout,
    normalize_base_url,
    path_segment,
)
from ._jcs_canonical import JcsCanonicalizationError, jcs_commitment
from ._retry import (
    RetryConfig,
    compute_backoff_s,
    is_retryable_status,
    parse_retry_after_s,
    resolve_retry_config,
)
from .agents import _UUID_RE
from .exceptions import (
    ForbiddenError,
    InteractionDecisionUnavailableError,
    InteractionDeniedError,
    InteractionTaskNotLiveError,
    PraesidiaConfigError,
    PraesidiaError,
    ResponseTooLargeError,
    _is_outage,
)

#: be ``InteractionType`` (``be/src/protected-actions/interaction-type.ts``), same order.
INTERACTION_TYPES = (
    "prompt_to_model",
    "model_to_tool",
    "agent_to_mcp",
    "agent_to_api",
    "agent_to_agent",
    "agent_to_db",
    "agent_to_saas",
    "agent_to_browser",
    "agent_to_code_execution",
    "agent_to_shell",
    "agent_to_filesystem",
    "agent_to_email",
)
#: be ``InteractionVerdict`` (``interaction-decision.dto.ts``).
INTERACTION_VERDICTS = ("allow", "deny", "require_approval")
#: be ``InteractionOutcomeStatus`` (BE-1582), same order.
INTERACTION_OUTCOME_STATUSES = ("succeeded", "failed_no_effect", "partial", "unknown")
#: Require an authorization decision for every hook; fail-open is an explicit opt-in.
DEFAULT_FAIL_MODES: Mapping[str, str] = MappingProxyType(
    {"tool_call": "closed", "exec": "closed", "fs_read": "closed", "fs_write": "closed", "browser": "closed"}
)

_ACTION_NAME = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*")
_MAX_CACHE_ENTRIES = 1000
_MAX_DECISION_BYTES = 64 * 1024
_NO_DECISION = (httpx.RequestError, PraesidiaError)


#: be ``InteractionConstrainedBy`` (BE-1609): the layer that denied or required approval.
INTERACTION_CONSTRAINED_BY = ("org_policy", "delegation", "assurance")


class _InteractionDecisionRequired(TypedDict):
    verdict: str
    reasonCode: str
    approvalId: str | None
    policyFingerprint: str
    ttlSeconds: int
    enforcementMode: str
    decisionId: str


class InteractionDecision(_InteractionDecisionRequired, total=False):
    """be ``InteractionDecisionResponseDto``, wire (camelCase) keys.

    ``constrainedBy`` (BE-1609) is absent from older servers; ``None`` = nothing constrained it.
    """

    constrainedBy: str | None


class InteractionDecisionRecordDetails(TypedDict, total=False):
    """SDK-2800 -- delegation keys be BE-2836 adds to the ``details`` of an ``interaction.decision``
    Decision Record (an ``audit.list()`` row). Absent from older servers and from decisions they do
    not apply to; every other ``details`` key stays untyped here.

    ``delegationReason``: the caller sent no capability token and was held to the live delegated
    tasks it executes. ``constrainingTaskId``: with it, the live task whose envelope decided
    (``None`` = none narrowed the verdict). ``delegationBypass``: an owner-level human decided
    without a token, so no task envelope applied.
    """

    delegationReason: Literal["delegation_implicit_live_task"]
    constrainingTaskId: str | None
    delegationBypass: Literal["owner"]


class _InteractionOutcomeReceiptRequired(TypedDict):
    approvalId: str | None
    decisionId: str


class InteractionOutcomeReceipt(_InteractionOutcomeReceiptRequired, total=False):
    """be ``InteractionOutcomeResponseDto``: ``decisionId`` is the Decision Record id of the outcome.

    ``approvalId`` is ``None`` and ``reportedDecisionId`` echoes the request on the
    ``decision_id`` path (BE-1808); ``reportedDecisionId`` is absent from older servers.
    """

    reportedDecisionId: str | None


class _OutcomeKeyError(PraesidiaConfigError, ValueError):
    """``report_outcome`` got neither or both of ``approval_id`` / ``decision_id``."""


@dataclass(frozen=True)
class InteractionHookResult:
    """
    What an allowing hook returns. ``decision`` is ``None`` only when the decision API was
    unavailable and the hook failed open; ``fail_open_error`` is then the reason.
    """

    decision: InteractionDecision | None
    fail_open_error: BaseException | None = None


_R = TypeVar("_R")


class _InteractionHooksBase(Generic[_R]):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        org_id: str | None = None,
        agent_id: str | None = None,
        base_url: str | None = None,
        allow_insecure_http: bool | None = None,
        timeout: float | None = None,
        fail_mode: Mapping[str, str] | None = None,
        approval_poll_interval: float = 2.0,
        approval_timeout: float = 600.0,
        on_approval_required: Callable[[InteractionDecision], None] | None = None,
        task_id: str | None = None,
        retry: RetryConfig | bool | None = None,
    ) -> None:
        api_key = api_key or os.environ.get("PRAESIDIA_API_KEY")
        org_id = org_id or os.environ.get("PRAESIDIA_ORG_ID")
        agent_id = agent_id or os.environ.get("PRAESIDIA_AGENT_ID")
        if not (api_key and org_id and agent_id):
            raise PraesidiaConfigError("Interaction hooks require api_key, org_id and agent_id")
        self._fail_modes = dict(DEFAULT_FAIL_MODES)
        for cls, mode in (fail_mode or {}).items():
            if cls not in DEFAULT_FAIL_MODES or mode not in ("open", "closed"):
                raise PraesidiaConfigError(f"fail_mode[{cls!r}] must be 'open' or 'closed' for a known hook class")
            self._fail_modes[cls] = mode
        self._poll = _positive(approval_poll_interval, "approval_poll_interval")
        self._approval_timeout = _positive(approval_timeout, "approval_timeout")
        self._on_approval_required = on_approval_required
        if task_id is not None and not (isinstance(task_id, str) and _UUID_RE.match(task_id)):
            raise PraesidiaConfigError("task_id must be an RFC-4122 UUID")
        #: BE-1609 — the agent task these hooks run under; its delegation envelope narrows every verdict.
        self.task_id = task_id
        self.organization_id = org_id
        self.agent_id = agent_id
        org = path_segment(org_id, "org_id")
        self._path = f"/organizations/{org}/interaction-decisions"
        #: Keyed by sha256(JCS({interactionType, action})); valid only under ``_fingerprint``.
        self._cache: dict[str, tuple[InteractionDecision, float]] = {}
        self._fingerprint: str | None = None
        self._lock = threading.Lock()
        try:
            retry_config = resolve_retry_config(retry)
        except ValueError as exc:
            raise PraesidiaConfigError(str(exc)) from exc
        self._http = self._open(
            retry_config,
            base_url=normalize_base_url(base_url or os.environ.get("PRAESIDIA_BASE_URL") or "https://api.praesidia.ai", allow_insecure_http),
            headers={
                "Authorization": f"Bearer {_validate_api_key(api_key)}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=_validate_timeout(_DEFAULT_TIMEOUT if timeout is None else timeout),
        )

    def _open(self, retry: RetryConfig | None, **client_args: Any) -> Any:
        raise NotImplementedError

    def _guard(self, interaction_type: str, action: Mapping[str, Any], fail_mode: str) -> _R:
        raise NotImplementedError

    def before_tool_call(self, tool_name: str, arguments: Mapping[str, Any] | None = None) -> _R:
        """Tool call chosen by the model: ``model_to_tool.<tool_name>``. Default fail-closed."""
        return self._guard("model_to_tool", _action(tool_name, arguments), self._fail_modes["tool_call"])

    def before_exec(
        self, command: str, args: Sequence[str] | None = None, cwd: str | None = None, runtime: str = "shell"
    ) -> _R:
        """Shell command (``agent_to_shell.exec``) or code run (``runtime="code"``: ``agent_to_code_execution.exec``). Default fail-closed."""
        if runtime not in ("shell", "code"):
            raise PraesidiaConfigError("runtime must be 'shell' or 'code'")
        kind = "agent_to_code_execution" if runtime == "code" else "agent_to_shell"
        arguments = {"command": command, "args": None if args is None else list(args), "cwd": cwd}
        return self._guard(kind, _action("exec", arguments), self._fail_modes["exec"])

    def before_fs_access(self, path: str, mode: str) -> _R:
        """Filesystem access: ``agent_to_filesystem.<mode>``. Default fail-closed for every mode (``fs_read`` / ``fs_write``)."""
        cls = "fs_read" if mode in ("read", "list") else "fs_write"
        return self._guard("agent_to_filesystem", _action(mode, {"path": path}), self._fail_modes[cls])

    def before_browser_action(
        self, action: str, url: str | None = None, arguments: Mapping[str, Any] | None = None
    ) -> _R:
        """Browser action: ``agent_to_browser.<action>``. Default fail-closed."""
        return self._guard("agent_to_browser", _action(action, {**(arguments or {}), "url": url}), self._fail_modes["browser"])

    def before_interaction(self, interaction_type: str, action: Mapping[str, Any], *, fail_mode: str = "closed") -> _R:
        """Any other interaction type (e.g. ``agent_to_email``), ``action = {"name", "arguments"?}``. Default fail-closed."""
        if fail_mode not in ("open", "closed"):
            raise PraesidiaConfigError("fail_mode must be 'open' or 'closed'")
        return self._guard(interaction_type, action, fail_mode)

    def _send(self, interaction_type: str, act: dict[str, Any], approval_id: str | None, key: str | None = None) -> Any:
        # One Idempotency-Key per call (BE-1759): retries replay it; a poll adds approvalId, so it is a new call. Key order = be DTO = TS SDK.
        return self._http.post(self._path, {
            "interactionType": interaction_type,
            "agentId": self.agent_id,
            "action": act,
            **({} if approval_id is None else {"approvalId": approval_id}),
            **({} if self.task_id is None else {"taskId": self.task_id}),
        }, functools.partial(_decision, task_id=self.task_id), _idempotency_key(key))

    def report_outcome(
        self,
        approval_id: str | None = None,
        status: str | None = None,
        *,
        decision_id: str | None = None,
        result: Any = None,
        target_system: str | None = None,
        target_transaction_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        """
        Record the result of an approved interaction, once (BE-1582). ``approval_id`` is
        ``decision["approvalId"]`` of an ``allow`` with reasonCode ``approval_consumed``; for a plain
        ``allow`` (``approvalId`` ``None``) pass ``decision_id=decision["decisionId"]`` instead (BE-1808).
        Exactly one of the two, else a ``PraesidiaConfigError`` that is also a ``ValueError``. ``result`` is
        committed locally (sha256 of its JCS form) and never sent; ``None`` sends no commitment. Any
        refusal (unknown, not consumed, not approved, already reported) is one ``PraesidiaError`` with
        ``status_code`` 409 (403 when the decision was issued to another principal); it is not retried. Returns an ``InteractionOutcomeReceipt``.
        ``idempotency_key`` (default: a fresh UUID v4) is sent as ``Idempotency-Key`` and reused by the SDK's retries.
        """
        keys = {k: v for k, v in (("approvalId", approval_id), ("decisionId", decision_id)) if v is not None}
        if len(keys) != 1:
            raise _OutcomeKeyError("pass exactly one of approval_id or decision_id")
        ((key, value),) = keys.items()
        if not isinstance(value, str) or not value:
            raise _OutcomeKeyError(f"{'approval_id' if key == 'approvalId' else 'decision_id'} must be a non-empty string")
        if status not in INTERACTION_OUTCOME_STATUSES:
            raise PraesidiaConfigError(f"status must be one of {', '.join(INTERACTION_OUTCOME_STATUSES)}")
        try:
            commitment = None if result is None else jcs_commitment(result)
        except JcsCanonicalizationError as exc:
            raise PraesidiaConfigError(f"result must be a JSON value: {exc}") from exc
        optional = {"resultCommitment": commitment, "targetSystem": target_system, "targetTransactionId": target_transaction_id}
        body = {"agentId": self.agent_id, key: value, "status": status}
        key = _idempotency_key(idempotency_key)  # validated before anything is sent
        # Key order = be DTO = TS SDK; a retry under the same key replays the recorded outcome (BE-1759).
        return self._http.post(f"{self._path}/outcome", {**body, **{k: v for k, v in optional.items() if v is not None}}, _receipt, key)

    def _cached(self, key: str) -> InteractionDecision | None:
        with self._lock:
            hit = self._cache.get(key)
        return hit[0] if hit is not None and hit[1] > time.monotonic() else None

    def _record(self, key: str, decision: InteractionDecision) -> InteractionDecision:
        """Cache a fresh decision; a new policy fingerprint evicts every cached verdict (BE-0328 pattern)."""
        with self._lock:
            if decision["policyFingerprint"] != self._fingerprint:
                self._cache.clear()
                self._fingerprint = decision["policyFingerprint"]
            if decision["ttlSeconds"] > 0 and decision["verdict"] != "require_approval":
                if len(self._cache) >= _MAX_CACHE_ENTRIES:
                    del self._cache[next(iter(self._cache))]
                self._cache[key] = (decision, time.monotonic() + decision["ttlSeconds"])
        return decision

    def _no_decision(self, err: BaseException, interaction_type: str, act: dict[str, Any], fail_mode: str) -> InteractionHookResult:
        if not _is_outage(err):
            raise err
        if fail_mode == "closed":
            raise InteractionDecisionUnavailableError(interaction_type, act["name"], err) from err
        return InteractionHookResult(None, err)

    def _approval_deadline(self, pending: InteractionDecision) -> float:
        if self._on_approval_required is not None:
            self._on_approval_required(pending)
        return time.monotonic() + self._approval_timeout

    @staticmethod
    def _finish(interaction_type: str, act: dict[str, Any], decision: InteractionDecision) -> InteractionHookResult:
        if decision["verdict"] != "allow":  # deny, or a require_approval wait that timed out
            reason = decision["reasonCode"] if decision["verdict"] == "deny" else "approval_wait_timeout"
            raise InteractionDeniedError(interaction_type, act["name"], reason, decision)  # type: ignore[arg-type]
        return InteractionHookResult(decision)


class PraesidiaInteractionHooks(_InteractionHooksBase[InteractionHookResult]):
    """Blocking hooks over one pooled ``httpx.Client`` (thread-safe). ``close()`` it, or use ``with``."""

    def _open(self, retry: RetryConfig | None, **client_args: Any) -> _SyncPost:
        return _SyncPost(httpx.Client(**client_args), retry)

    def decide(
        self, interaction_type: str, action: Mapping[str, Any], approval_id: str | None = None, idempotency_key: str | None = None
    ) -> InteractionDecision:
        """One raw decision request: no cache, no approval wait, no fail mode. ``idempotency_key``: see README."""
        return self._send(interaction_type, _check(interaction_type, action)[1], approval_id, idempotency_key)

    def guarded(self, tool: Callable[..., Any], tool_name: str | None = None) -> Callable[..., Any]:
        """Wrap a keyword-argument tool so every call runs ``before_tool_call`` first (name: ``tool.__name__``)."""
        name = _tool_name(tool, tool_name)

        @functools.wraps(tool)
        def call(**arguments: Any) -> Any:
            self.before_tool_call(name, arguments)
            return tool(**arguments)

        return call

    def _guard(self, interaction_type: str, action: Mapping[str, Any], fail_mode: str) -> InteractionHookResult:
        key, act = _check(interaction_type, action)
        decision = self._cached(key)
        if decision is None:
            try:
                decision = self._record(key, self._send(interaction_type, act, None))
            except _NO_DECISION as err:
                return self._no_decision(err, interaction_type, act, fail_mode)
        if decision["verdict"] == "require_approval":
            decision = self._await_approval(key, interaction_type, act, decision)
        return self._finish(interaction_type, act, decision)

    def _await_approval(self, key: str, interaction_type: str, act: dict[str, Any], pending: InteractionDecision) -> InteractionDecision:
        """Re-POST echoing ``approvalId`` until the verdict changes; an outage keeps waiting, never allows."""
        deadline = self._approval_deadline(pending)
        current = pending
        while time.monotonic() < deadline:
            time.sleep(min(self._poll, max(0.0, deadline - time.monotonic())))
            try:
                current = self._record(key, self._send(interaction_type, act, current["approvalId"]))
            except _NO_DECISION as err:
                if not _is_outage(err):
                    raise
                continue
            if current["verdict"] != "require_approval":
                break
        return current

    def close(self) -> None:
        self._http.client.close()

    def __enter__(self) -> PraesidiaInteractionHooks:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class AsyncPraesidiaInteractionHooks(_InteractionHooksBase[Awaitable[InteractionHookResult]]):
    """The same hooks over one ``httpx.AsyncClient``; every hook is awaited. ``await aclose()``, or ``async with``."""

    def _open(self, retry: RetryConfig | None, **client_args: Any) -> _AsyncPost:
        return _AsyncPost(httpx.AsyncClient(**client_args), retry)

    async def decide(
        self, interaction_type: str, action: Mapping[str, Any], approval_id: str | None = None, idempotency_key: str | None = None
    ) -> InteractionDecision:
        """One raw decision request: no cache, no approval wait, no fail mode. ``idempotency_key``: see README."""
        return await self._send(interaction_type, _check(interaction_type, action)[1], approval_id, idempotency_key)

    def guarded(self, tool: Callable[..., Any], tool_name: str | None = None) -> Callable[..., Awaitable[Any]]:
        """Wrap a keyword-argument tool (sync or async) so every call awaits ``before_tool_call`` first."""
        name = _tool_name(tool, tool_name)

        @functools.wraps(tool)
        async def call(**arguments: Any) -> Any:
            await self.before_tool_call(name, arguments)
            result = tool(**arguments)
            return await result if inspect.isawaitable(result) else result

        return call

    async def _guard(self, interaction_type: str, action: Mapping[str, Any], fail_mode: str) -> InteractionHookResult:
        key, act = _check(interaction_type, action)
        decision = self._cached(key)
        if decision is None:
            try:
                decision = self._record(key, await self._send(interaction_type, act, None))
            except _NO_DECISION as err:
                return self._no_decision(err, interaction_type, act, fail_mode)
        if decision["verdict"] == "require_approval":
            decision = await self._await_approval(key, interaction_type, act, decision)
        return self._finish(interaction_type, act, decision)

    async def _await_approval(self, key: str, interaction_type: str, act: dict[str, Any], pending: InteractionDecision) -> InteractionDecision:
        deadline = self._approval_deadline(pending)
        current = pending
        while time.monotonic() < deadline:
            await asyncio.sleep(min(self._poll, max(0.0, deadline - time.monotonic())))
            try:
                current = self._record(key, await self._send(interaction_type, act, current["approvalId"]))
            except _NO_DECISION as err:
                if not _is_outage(err):
                    raise
                continue
            if current["verdict"] != "require_approval":
                break
        return current

    async def aclose(self) -> None:
        await self._http.client.aclose()

    async def __aenter__(self) -> AsyncPraesidiaInteractionHooks:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()


class _SyncPost:
    def __init__(self, client: httpx.Client, retry: RetryConfig | None) -> None:
        self.client = client
        self.retry = retry

    def post(self, path: str, json: dict[str, Any], parse: Callable[..., Any], key: str) -> Any:
        content, headers, start, attempt = _dumps(json), {"Idempotency-Key": key}, time.monotonic(), 1
        while True:
            raw = bytearray()
            try:
                with self.client.stream("POST", path, content=content, headers=headers) as r:
                    for chunk in r.iter_bytes():
                        _append(raw, chunk, r)
            except httpx.RequestError:
                if (delay := _retry_delay(self.retry, attempt, start, None)) is None:
                    raise
            else:
                if (delay := _retry_delay(self.retry, attempt, start, r)) is None:
                    return parse(r, raw)
            time.sleep(delay)
            attempt += 1


class _AsyncPost:
    def __init__(self, client: httpx.AsyncClient, retry: RetryConfig | None) -> None:
        self.client = client
        self.retry = retry

    async def post(self, path: str, json: dict[str, Any], parse: Callable[..., Any], key: str) -> Any:
        content, headers, start, attempt = _dumps(json), {"Idempotency-Key": key}, time.monotonic(), 1
        while True:
            raw = bytearray()
            try:
                async with self.client.stream("POST", path, content=content, headers=headers) as r:
                    async for chunk in r.aiter_bytes():
                        _append(raw, chunk, r)
            except httpx.RequestError:
                if (delay := _retry_delay(self.retry, attempt, start, None)) is None:
                    raise
            else:
                if (delay := _retry_delay(self.retry, attempt, start, r)) is None:
                    return parse(r, raw)
            await asyncio.sleep(delay)
            attempt += 1


def _idempotency_key(key: str | None) -> str:
    """A caller key, checked like be's (at most 255 chars) plus a printable-ASCII header value; else a fresh UUID v4."""
    if key is None:
        return str(uuid.uuid4())
    try:
        key = _validate_idempotency_key(key)
    except ValueError as exc:
        raise PraesidiaConfigError(str(exc)) from exc
    if len(key) > 255 or not key.isascii():
        raise PraesidiaConfigError("idempotency_key must be at most 255 printable ASCII characters")
    return key


def _retry_delay(cfg: RetryConfig | None, attempt: int, start: float, r: httpx.Response | None) -> float | None:
    """
    Seconds to wait before re-sending the same key, or ``None`` to stop. Retried: a transport error
    (``r`` is ``None``) or 429/5xx, never a 409 or any other 4xx (TS SDK-2503). Budget and backoff as
    for the management client.
    """
    if cfg is None or attempt >= cfg.max_attempts:
        return None
    if r is not None and not is_retryable_status(r.status_code):
        return None
    retry_after = None if r is None else parse_retry_after_s(r.headers.get("retry-after"))
    delay = retry_after if retry_after is not None else compute_backoff_s(attempt, cfg.base_delay_s, cfg.max_delay_s)
    return None if time.monotonic() - start + delay >= cfg.max_elapsed_s else delay


def _dumps(body: dict[str, Any]) -> bytes:
    """``JSON.stringify`` bytes, so both SDKs send the same body for the same request."""
    return json.dumps(body, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _append(raw: bytearray, chunk: bytes, r: httpx.Response) -> None:
    raw.extend(chunk)
    if len(raw) > _MAX_DECISION_BYTES:
        raise ResponseTooLargeError(r.request.url.path, _MAX_DECISION_BYTES, r.status_code)


def _json(r: httpx.Response, raw: bytearray) -> Any:
    """Typed SDK error for a non-2xx; the parsed 2xx body, or ``None`` when it is not JSON."""
    HttpClient._raise_for_status(httpx.Response(r.status_code, content=bytes(raw), request=r.request))
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _receipt(r: httpx.Response, raw: bytearray) -> InteractionOutcomeReceipt:
    d = _json(r, raw)
    if not (
        isinstance(d, dict)
        and isinstance(d.get("decisionId"), str)
        and (isinstance(d.get("approvalId"), str) or isinstance(d.get("reportedDecisionId"), str))
    ):
        raise PraesidiaError("malformed interaction outcome response", status_code=r.status_code)
    return d  # type: ignore[return-value]


#: be BE-2836's 403 message (no ``code`` is sent) for a token-less ``taskId`` that is not a live task.
_TASK_NOT_LIVE = "taskId is not a live task this agent executes"


def _decision(r: httpx.Response, raw: bytearray, task_id: str | None = None) -> InteractionDecision:
    """Typed SDK error for a non-2xx; a malformed 2xx raises ``PraesidiaError`` with its 2xx status (an outage)."""
    try:
        d = _json(r, raw)
    except ForbiddenError as err:
        if task_id is None or not isinstance(err.body, dict) or err.body.get("message") != _TASK_NOT_LIVE:
            raise
        raise InteractionTaskNotLiveError(
            task_id, err.message, code=err.code, request_id=err.request_id, details=err.details,
            retry_after=err.retry_after, retryable=err.retryable, body=err.body,
        ) from err
    ttl = d.get("ttlSeconds") if isinstance(d, dict) else None
    if not (
        isinstance(d, dict)
        and d.get("verdict") in INTERACTION_VERDICTS
        and isinstance(d.get("reasonCode"), str)
        and isinstance(d.get("policyFingerprint"), str)
        and type(ttl) is int
        and ttl >= 0
        and (d["verdict"] != "require_approval" or isinstance(d.get("approvalId"), str))
    ):
        raise PraesidiaError("malformed interaction decision response", status_code=r.status_code)
    return d  # type: ignore[return-value]


def _action(name: str, arguments: Mapping[str, Any] | None) -> dict[str, Any]:
    """Drop ``None`` argument values (the TS SDK drops ``undefined``); omit ``arguments`` when nothing is left."""
    if arguments is not None and not isinstance(arguments, Mapping):
        raise PraesidiaConfigError("action arguments must be a JSON object")
    kept = {k: v for k, v in (arguments or {}).items() if v is not None}
    return {"name": name, "arguments": kept} if kept else {"name": name}


def _check(interaction_type: str, action: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Validate a request with be's rules before anything is sent; return (cache key, wire action)."""
    if interaction_type not in INTERACTION_TYPES:
        raise PraesidiaConfigError(f"interaction_type must be one of {', '.join(INTERACTION_TYPES)}")
    name = action.get("name") if isinstance(action, Mapping) else None
    if not isinstance(name, str) or len(name) > 200 or not _ACTION_NAME.fullmatch(name):
        raise PraesidiaConfigError("action name must be dot-separated segments of [A-Za-z0-9_-], at most 200 chars")
    args = action.get("arguments")
    if args is not None and not isinstance(args, Mapping):
        raise PraesidiaConfigError("action arguments must be a JSON object")
    act = {"name": name} if args is None else {"name": name, "arguments": dict(args)}
    try:
        return jcs_commitment({"interactionType": interaction_type, "action": act}), act
    except JcsCanonicalizationError as exc:
        raise PraesidiaConfigError(f"action arguments must be JSON values: {exc}") from exc


def _tool_name(tool: Callable[..., Any], tool_name: str | None) -> str:
    name = tool_name if tool_name is not None else getattr(tool, "__name__", None)
    _check("model_to_tool", {"name": name})
    return name  # type: ignore[return-value]


def _positive(value: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise PraesidiaConfigError(f"{label} must be a positive number of seconds")
    return float(value)
