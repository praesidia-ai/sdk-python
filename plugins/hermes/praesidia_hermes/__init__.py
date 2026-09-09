"""Managed effects require native execution context and live backend authority."""
from __future__ import annotations

from contextvars import ContextVar
import json
import os
from typing import Any

from praesidia import Praesidia
from praesidia.integrations import FileRuntimeAttemptStore
from praesidia.integrations.protected_tool import (
    ManagedProtectedTool, RuntimeBinding, RuntimeCall, decode_body,
)

TOOL_NAME = "praesidia_protected_http"
SCHEMA = {"name": TOOL_NAME, "description": "Request an independently approved managed HTTP effect. Re-enter the same native call after external approval.",
          "parameters": {"type": "object", "properties": {"body": {"type": "object", "additionalProperties": True}},
                         "required": ["body"], "additionalProperties": False}}


class _State:
    """PluginState performs locked atomic mode-0600 writes on each assignment."""
    def __init__(self, state):
        self.state = state

    def get(self, key, default=None):
        return self.state.get(key, default)

    def __setitem__(self, key, value):
        self.state.set(key, value)


def register(ctx):
    """Install in an explicitly enabled Hermes profile; strict tools by default.

    Strict mode is a cooperative in-process boundary, not an OS sandbox. Hermes
    may skip failing hooks/middleware, so managed dispatch also requires a
    private ContextVar minted only by this plugin's successful middleware.
    """
    strict = ctx.get_config("strict_tools", True)
    if type(strict) is not bool:
        raise ValueError("praesidia strict_tools must be a Boolean")
    target = ctx.get_config("target_id")
    # Credentials are read at installation, never from tool arguments/state.
    client = Praesidia(api_key=os.environ["PRAESIDIA_API_KEY"], org_id=os.environ["PRAESIDIA_ORG_ID"],
                       base_url=os.environ["PRAESIDIA_API_URL"])
    managed = ManagedProtectedTool(client.protected_http, target_id=target, name=TOOL_NAME,
                                   description=SCHEMA["description"],
                                   attempt_store=FileRuntimeAttemptStore(ctx.state.path.parent / "attempts"))
    native: ContextVar[Any] = ContextVar("praesidia_hermes_native_call", default=None)
    state = _State(ctx.state)

    def pre_tool_call(*, tool_name=None, args=None, session_id=None, tool_call_id=None, **_):
        if tool_name != TOOL_NAME:
            return {"action": "block", "message": "This profile permits only the Praesidia managed tool."} if strict else None
        try:
            RuntimeCall("hermes", session_id, tool_call_id).checkpoint(TOOL_NAME)
            decode_body(json.dumps(args, allow_nan=False))
        except Exception:
            return {"action": "block", "message": "Managed tool requires valid native session, call and JSON arguments."}
        return None

    def execution(*, tool_name=None, args=None, session_id=None, tool_call_id=None, next_call=None, **_):
        if tool_name != TOOL_NAME:
            if strict:
                return json.dumps({"error": "This profile permits only the Praesidia managed tool."})
            return next_call(args)
        # Never throw before returning a denial: Hermes skips middleware that
        # raises before next_call. Handler independently rejects absent context.
        try:
            call = RuntimeCall("hermes", session_id, tool_call_id)
            call.checkpoint(TOOL_NAME)
            decode_body(json.dumps(args, allow_nan=False))
        except Exception:
            return json.dumps({"error": "Managed tool requires valid native session, call and JSON arguments."})
        token = native.set(call)
        try:
            return next_call(args)
        finally:
            native.reset(token)

    def handler(args, *, session_id=None, **_):
        call = native.get()
        if not isinstance(call, RuntimeCall) or call.thread_id != session_id:
            return json.dumps({"error": "Praesidia execution middleware and matching native session are required."})
        try:
            body = decode_body(json.dumps(args, allow_nan=False))
            return json.dumps(managed.invoke(body, RuntimeBinding(call, state)), allow_nan=False)
        except Exception:
            # Do not expose HTTP headers, response bodies or signing material in
            # Hermes' generic exception logger. Durable state permits inspection.
            return json.dumps({"error": "Managed request failed closed; inspect its Praesidia checkpoint before retrying."})

    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_middleware("tool_execution", execution)
    handle = ctx.register_tool(name=TOOL_NAME, toolset="praesidia", schema=SCHEMA,
                               handler=handler, description=SCHEMA["description"])
    if handle is None:
        raise RuntimeError("Praesidia tool registration was refused")
