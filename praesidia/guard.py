"""
Praesidia SDK — ``Guard``: the batteries-included guardrail + audit wrapper.

TOP-0008 — the Python-idiomatic equivalent of the TS SDK's ``PraesidiaGuard``
(``sdk/src/guard.ts``), including its offline rule-based guardrail fallback
(see :mod:`praesidia.local_rules`).

``Guard`` deliberately does NOT re-expose ``protect_action`` or
``refresh_credential`` — those already have first-class, tested homes on
:class:`praesidia.client.Praesidia` / :class:`praesidia.agents.AgentsResource`
and duplicating them here would just be two ways to reach the same code with
no behavioural difference. Use ``Praesidia(...).agents.protect_action(...)``
/ ``Praesidia(...).refresh_credential(...)`` directly when you need those.
This is a documented, intentional surface difference from the TS SDK, whose
single ``PraesidiaGuard`` class happens to own every management primitive.

Idiomatic-Python additions over the TS callback-closure shape:
  - :meth:`Guard.protect` — a decorator, the natural Python analogue of
    wrapping a call with cross-cutting guardrail + audit behaviour.
  - :class:`TaskHandle` doubles as a context manager: exiting a ``with``
    block on an exception calls ``fail()`` automatically, removing the
    manual try/except/finally a bare begin/complete/fail lifecycle needs.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar, Union

from ._http import CHAIN_ID_HEADER, HttpClient
from ._retry import RetryConfig
from .exceptions import GuardrailBlockedError, PraesidiaConfigError
from .local_rules import run_local_rules

_LOGGER = logging.getLogger("praesidia.guard")

_DEFAULT_BASE_URL = "https://api.praesidia.ai"

#: AUDIT-SDK-02 parity — ``CreateAgentTaskDto.chainId`` is ``@IsUUID``, so
#: only a real UUID may be forwarded as ``chainId``.
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_F = TypeVar("_F", bound=Callable[..., Any])


def _is_uuid(value: str) -> bool:
    return bool(_UUID_RE.match(value))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _stringify(value: Any) -> str:
    """Safely convert an arbitrary value to a string for content checks."""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return str(value)


def _build_task_input(task: dict[str, Any]) -> dict[str, Any]:
    """
    AUDIT-SDK-02 parity — build the DTO-required non-empty ``input`` OBJECT
    for ``CreateAgentTaskDto``. A string ``input`` is wrapped as
    ``{"message": ...}``; a dict ``input`` is merged as-is. The SDK's own
    telemetry (``output``/``usage``/``context``/...) is nested under
    ``input`` — those are not top-level DTO fields.
    """
    result: dict[str, Any] = {}
    raw = task.get("input")
    if isinstance(raw, str):
        result["message"] = raw
    elif isinstance(raw, dict):
        result.update(raw)
    for key in ("output", "usage", "context", "taskType", "status", "startedAt", "completedAt"):
        if task.get(key) is not None:
            result[key] = task[key]
    if not result:
        result["message"] = ""
    return result


def _build_task_body(
    connection_id: str, task_type: str, task_input: dict[str, Any], chain_id: str | None
) -> dict[str, Any]:
    body: dict[str, Any] = {"connectionId": connection_id, "type": task_type, "input": task_input}
    if chain_id and _is_uuid(chain_id):
        body["chainId"] = chain_id
    return body


class TaskHandle:
    """
    Returned by :meth:`Guard.begin_task`. Records EXACTLY ONE audit task row
    when ``complete()``/``fail()`` is called — never two — so a lifecycle
    maps 1:1 to a single task. The first ``complete``/``fail`` call wins
    (memoized, including a re-raised exception on repeat calls); later calls
    return/raise the same outcome without another write.

    Also usable as a context manager: exiting the ``with`` block on an
    exception calls ``fail()`` automatically (best-effort, memoized) before
    the exception propagates::

        with guard.begin_task(input=prompt) as task:
            output = call_my_llm(prompt)
            task.complete(output)
    """

    def __init__(
        self,
        record: Callable[[str, str | None, dict[str, Any] | None, dict[str, Any] | None], str | None],
    ) -> None:
        self._record = record
        self._finalized = False
        self._result: str | None = None
        self._error: BaseException | None = None

    def complete(
        self, output: str | None = None, *, usage: dict[str, Any] | None = None, context: dict[str, Any] | None = None
    ) -> str | None:
        return self._finalize_once("completed", output, usage, context)

    def fail(
        self, error: BaseException | str, *, usage: dict[str, Any] | None = None, context: dict[str, Any] | None = None
    ) -> str | None:
        message = error if isinstance(error, str) else str(error)
        return self._finalize_once("failed", message, usage, context)

    def _finalize_once(
        self, status: str, output: str | None, usage: dict[str, Any] | None, context: dict[str, Any] | None
    ) -> str | None:
        if not self._finalized:
            self._finalized = True
            try:
                self._result = self._record(status, output, usage, context)
            except BaseException as exc:  # noqa: BLE001 -- memoized and re-raised, never swallowed
                self._error = exc
                raise
            return self._result
        if self._error is not None:
            raise self._error
        return self._result

    def __enter__(self) -> "TaskHandle":
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> bool:
        if exc is not None:
            self.fail(exc)
        return False  # never suppress the exception


class Guard:
    """
    ``Guard`` — the primary convenience entry point for the ``praesidia``
    package's guardrail + audit-logging surface.

    Zero config (reads from env vars)::

        guard = Guard()
        result = guard.run(lambda: call_my_llm(prompt), input=prompt)

    Config resolution order: constructor arg -> environment variable -> default.

    Fail-open / fail-closed behaviour:
      - Input blocks ALWAYS raise :class:`~praesidia.exceptions.GuardrailBlockedError`
        before the wrapped call runs.
      - Output blocks are returned for inspection by default; ``strict=True``
        (or ``guard_output(..., throw_on_block=True)``) raises instead.
      - Network errors talking to Praesidia: by default (``fail_open=False,
        strict=False``) they are logged via the ``praesidia.guard`` logger
        and treated as a local pass, so infrastructure failures never
        disrupt the caller's agent.
      - ``fail_open=True`` -> same as default (silent degradation).
      - ``strict=True``    -> network errors are re-raised.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        org_id: str | None = None,
        agent_id: str | None = None,
        connection_id: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        retry: Union[RetryConfig, bool, None] = None,
        strict: bool = False,
        fail_open: bool = False,
    ) -> None:
        self._api_key = api_key or os.environ.get("PRAESIDIA_API_KEY")
        self._org_id = org_id or os.environ.get("PRAESIDIA_ORG_ID")
        self._agent_id = agent_id or os.environ.get("PRAESIDIA_AGENT_ID")
        self._connection_id = connection_id or os.environ.get("PRAESIDIA_CONNECTION_ID")
        self._base_url = base_url or os.environ.get("PRAESIDIA_BASE_URL") or _DEFAULT_BASE_URL
        self.strict = strict
        self.fail_open = fail_open

        # Local/offline mode when no api_key + org_id are resolvable -- no
        # HttpClient is ever constructed, so no transport exists to make a
        # network call with (see tests/test_guard.py's
        # TestOfflineMakesZeroNetworkCalls).
        self._http: HttpClient | None = None
        if self._api_key and self._org_id:
            http_kwargs: dict[str, Any] = {}
            if timeout is not None:
                http_kwargs["timeout"] = timeout
            if retry is not None:
                http_kwargs["retry"] = retry
            self._http = HttpClient(
                api_key=self._api_key, org_id=self._org_id, base_url=self._base_url, **http_kwargs
            )

    # ── identity ────────────────────────────────────────────────────────

    @property
    def identity(self) -> dict[str, Any]:
        """
        The agent identity this guard operates as (org + agent + base URL).
        ``org_id``/``agent_id`` are ``None`` in local/offline mode;
        ``connected`` is true only when an authenticated client is configured.
        """
        return {
            "orgId": self._org_id,
            "agentId": self._agent_id,
            "baseUrl": self._base_url,
            "connected": self._http is not None,
        }

    def forward_chain(self, chain_id: str | None) -> None:
        """Adopt an inbound chain-trace id (see ``HttpClient.set_chain_id``). No-op offline."""
        if self._http is not None:
            self._http.set_chain_id(chain_id)

    # ── guardrail hooks ─────────────────────────────────────────────────

    def guard_input(
        self,
        content: str,
        *,
        agent_id: str | None = None,
        context: dict[str, Any] | None = None,
        chain_id: str | None = None,
    ) -> dict[str, Any]:
        """The guardrail PRE hook: check input and FAIL-CLOSED on a block."""
        result = self.check_input(content, agent_id=agent_id, context=context, chain_id=chain_id)
        if not result["passed"]:
            raise GuardrailBlockedError(result["triggered"])
        return result

    def guard_output(
        self,
        content: str,
        *,
        agent_id: str | None = None,
        context: dict[str, Any] | None = None,
        chain_id: str | None = None,
        throw_on_block: bool | None = None,
    ) -> dict[str, Any]:
        """The guardrail POST hook: fail-OPEN by convention unless ``throw_on_block``/``strict``."""
        result = self.check_output(content, agent_id=agent_id, context=context, chain_id=chain_id)
        should_throw = self.strict if throw_on_block is None else throw_on_block
        if not result["passed"] and should_throw:
            raise GuardrailBlockedError(result["triggered"])
        return result

    def check_input(
        self,
        content: str,
        *,
        agent_id: str | None = None,
        context: dict[str, Any] | None = None,
        chain_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Check input content against guardrails.

        Local mode (no API key): runs the bundled rule-based patterns
        (:func:`praesidia.local_rules.run_local_rules`) -- zero network calls.
        Connected mode: ``POST /organizations/:org_id/guardrails/validate``.
        """
        return self._check_content(content, "INPUT", agent_id=agent_id, context=context, chain_id=chain_id)

    def check_output(
        self,
        content: str,
        *,
        agent_id: str | None = None,
        context: dict[str, Any] | None = None,
        chain_id: str | None = None,
    ) -> dict[str, Any]:
        """Check output content against guardrails. See :meth:`check_input`."""
        return self._check_content(content, "OUTPUT", agent_id=agent_id, context=context, chain_id=chain_id)

    def _check_content(
        self,
        content: str,
        scope: str,
        *,
        agent_id: str | None,
        context: dict[str, Any] | None,
        chain_id: str | None,
    ) -> dict[str, Any]:
        if self._http is None:
            return run_local_rules(content)

        headers = {CHAIN_ID_HEADER: chain_id} if chain_id else None
        try:
            result = self._http.post(
                f"/organizations/{self._http.org_id}/guardrails/validate",
                json={
                    "content": content,
                    "agentId": agent_id or self._agent_id,
                    "scope": scope,
                    "context": context,
                },
                headers=headers,
            )
        except Exception as err:  # noqa: BLE001 -- must degrade on ANY transport/HTTP failure
            self._handle_network_error(err, "guardrails/validate")
            return run_local_rules(content)

        return {
            "passed": result["passed"],
            "triggered": result.get("triggered") or [],
            "processingTimeMs": result.get("processingTimeMs"),
            "requestId": result.get("requestId"),
            "local": False,
        }

    def _handle_network_error(self, err: BaseException, operation: str) -> None:
        """Swallow (fail_open/default) or re-raise (strict) a network/API error."""
        if self.fail_open:
            return
        if self.strict:
            raise err
        _LOGGER.warning("[praesidia] %s failed (degrading gracefully): %s", operation, err)

    # ── audit logging ───────────────────────────────────────────────────

    def log_task(self, task: dict[str, Any]) -> str | None:
        """
        Log a task to the Praesidia audit log. Returns the server-assigned
        task id, or ``None`` in local mode. Requires ``api_key`` + ``org_id``
        + a resolvable ``connection_id``; raises
        :class:`~praesidia.exceptions.PraesidiaConfigError` if missing and
        ``strict=True``, otherwise logs locally and returns ``None``.
        """
        if self._http is None:
            if self.strict:
                raise PraesidiaConfigError("log_task requires api_key and org_id (or PRAESIDIA_API_KEY/PRAESIDIA_ORG_ID)")
            self._console_log("task", task)
            return None

        connection_id = task.get("connectionId") or self._connection_id
        if not connection_id:
            if self.strict:
                raise PraesidiaConfigError(
                    "log_task requires a connection_id -- pass task['connectionId'] or set "
                    "connection_id on Guard (env PRAESIDIA_CONNECTION_ID)"
                )
            self._console_log("task:skipped(no connectionId)", task)
            return None

        chain_id = task.get("chainId") or self._http.get_chain_id()
        body = _build_task_body(connection_id, task.get("type") or "MESSAGE", _build_task_input(task), chain_id)
        headers = {CHAIN_ID_HEADER: chain_id} if chain_id else None
        try:
            res = self._http.post(f"/organizations/{self._http.org_id}/tasks", json=body, headers=headers)
            return res.get("id")
        except Exception as err:  # noqa: BLE001
            self._handle_network_error(err, "logTask")
            return None

    def _console_log(self, log_type: str, data: Any) -> None:
        print(json.dumps({"timestamp": _now_iso(), "praesidia": True, "type": log_type, "data": data}))

    def begin_task(
        self,
        *,
        input: str | None = None,  # noqa: A002 -- matches the DTO/JS field name
        agent_id: str | None = None,
        task_type: str = "run",
        context: dict[str, Any] | None = None,
        connection_id: str | None = None,
        type: str | None = None,  # noqa: A002 -- AgentTaskType, matches the DTO/JS field name
        chain_id: str | None = None,
    ) -> TaskHandle:
        """Open an explicit task-lifecycle handle. See :class:`TaskHandle`."""
        started_at = _now_iso()
        base = {
            "agentId": agent_id or self._agent_id,
            "input": input,
            "taskType": task_type,
            "context": context,
            "connectionId": connection_id,
            "type": type,
            "chainId": chain_id,
            "startedAt": started_at,
        }

        def record(
            status: str, output: str | None, usage: dict[str, Any] | None, finalize_context: dict[str, Any] | None
        ) -> str | None:
            merged_context = (
                {**(base["context"] or {}), **finalize_context} if finalize_context else base["context"]
            )
            return self.log_task(
                {
                    **base,
                    "output": output,
                    "usage": usage,
                    "context": merged_context,
                    "completedAt": _now_iso(),
                    "status": status,
                }
            )

        return TaskHandle(record)

    # ── the all-in-one wrapper ──────────────────────────────────────────

    def run(
        self,
        fn: Callable[[], Any],
        *,
        input: str,  # noqa: A002
        agent_id: str | None = None,
        task_type: str = "run",
        context: dict[str, Any] | None = None,
        connection_id: str | None = None,
        type: str | None = None,  # noqa: A002
        chain_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Call ``fn()`` with guardrail checks and audit logging.

        Flow: check_input (raise on block, ``fn`` NOT called) -> call ``fn()``
        -> check_output -> log_task -> raise on a strict output block,
        otherwise return ``{"output", "taskId", "inputCheck", "outputCheck"}``.
        """
        resolved_agent_id = agent_id or self._agent_id

        input_check = self.check_input(input, agent_id=resolved_agent_id, context=context, chain_id=chain_id)
        if not input_check["passed"]:
            raise GuardrailBlockedError(input_check["triggered"])

        started_at = _now_iso()
        try:
            output = fn()
        except Exception as error:
            try:
                self.log_task(
                    {
                        "agentId": resolved_agent_id,
                        "input": input,
                        "output": str(error),
                        "taskType": task_type,
                        "context": context,
                        "connectionId": connection_id,
                        "type": type,
                        "startedAt": started_at,
                        "completedAt": _now_iso(),
                        "status": "failed",
                        "chainId": chain_id,
                    }
                )
            except Exception:  # noqa: BLE001 -- audit recording is best-effort
                pass
            raise

        completed_at = _now_iso()
        output_str = _stringify(output)
        output_check = self.check_output(
            output_str, agent_id=resolved_agent_id, context=context, chain_id=chain_id
        )
        output_blocked = not output_check["passed"] and self.strict

        task_id = self.log_task(
            {
                "agentId": resolved_agent_id,
                "input": input,
                "output": output_str,
                "taskType": task_type,
                "context": context,
                "connectionId": connection_id,
                "type": type,
                "startedAt": started_at,
                "completedAt": completed_at,
                "status": "failed" if output_blocked else "completed",
                "chainId": chain_id,
            }
        )

        if output_blocked:
            raise GuardrailBlockedError(output_check["triggered"])

        return {"output": output, "taskId": task_id, "inputCheck": input_check, "outputCheck": output_check}

    # ── idiomatic Python addition: decorator form of run() ─────────────

    def protect(
        self,
        func: _F | None = None,
        *,
        task_type: str = "run",
        agent_id: str | None = None,
        context: dict[str, Any] | None = None,
        connection_id: str | None = None,
        type: str | None = None,  # noqa: A002
        chain_id: str | None = None,
    ) -> Any:
        """
        Decorator equivalent of :meth:`run` — the idiomatic Python entry
        point for wrapping an agent call, in place of TS's callback-closure
        shape (``guard.run(() => llmCall(prompt), { input: prompt })``).

        Decorates a callable whose FIRST positional argument is the string
        content to guard; returns the callable's own return value (unwrapped)
        and raises :class:`~praesidia.exceptions.GuardrailBlockedError`
        exactly like :meth:`run` on an input block or a strict output block,
        recording one best-effort audit task per call::

            @guard.protect(task_type="chat")
            def call_llm(prompt: str) -> str:
                return openai_call(prompt)

            reply = call_llm("hello")  # guarded + audited transparently

        Supports both bare ``@guard.protect`` and ``@guard.protect(...)``.
        """

        def decorator(inner: _F) -> _F:
            @functools.wraps(inner)
            def wrapper(content: str, *args: Any, **kwargs: Any) -> Any:
                result = self.run(
                    lambda: inner(content, *args, **kwargs),
                    input=content,
                    agent_id=agent_id,
                    task_type=task_type,
                    context=context,
                    connection_id=connection_id,
                    type=type,
                    chain_id=chain_id,
                )
                return result["output"]

            return wrapper  # type: ignore[return-value]

        if func is not None:
            return decorator(func)
        return decorator
