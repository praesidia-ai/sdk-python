"""OpenAI Agents Python function tool and native approval-interruption bridge."""
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable

from .protected_tool import ManagedProtectedTool, RuntimeBinding, RuntimeCall, decode_body


def openai_tool(managed: ManagedProtectedTool, *,
                binding: Callable[[Any, str], RuntimeBinding] | None = None):
    """Return a real FunctionTool. Persist RunState plus its host-owned context.

    Default context: {"praesidia_thread_id": actual_session_id,
    "praesidia_tools": {}}. Native approval is only a wake-up: the backend still
    requires the independently approved exact request. Hosted tools are excluded.
    """
    from agents import FunctionTool

    def resolve(context: Any, call_id: str) -> RuntimeBinding:
        if binding:
            result = binding(context, call_id)
        else:
            host = context.context
            if not isinstance(host, dict) or not isinstance(host.get("praesidia_tools"), dict):
                raise ValueError("OpenAI requires persisted host context with praesidia_tools")
            result = RuntimeBinding(RuntimeCall("openai-agents", host.get("praesidia_thread_id"), call_id), host["praesidia_tools"])
        if result.call.runtime != "openai-agents" or result.call.call_id != call_id:
            raise ValueError("OpenAI binding must match the native tool call")
        return result

    async def needs_approval(context: Any, arguments: dict[str, Any], call_id: str) -> bool:
        body = decode_body(json.dumps(arguments, allow_nan=False))
        view = await asyncio.to_thread(managed.prepare, body, resolve(context, call_id))
        return view["disposition"] == "approval_required"

    async def invoke(context: Any, arguments: str) -> str:
        if context.tool_name != managed.name:
            raise ValueError("OpenAI tool identity differs from the managed tool")
        result = await asyncio.to_thread(managed.invoke, decode_body(arguments), resolve(context, context.tool_call_id))
        return json.dumps(result, allow_nan=False)

    return FunctionTool(name=managed.name, description=managed.description,
                        params_json_schema={"type": "object", "properties": {"body": {"type": "object", "additionalProperties": True}},
                                            "required": ["body"], "additionalProperties": False},
                        strict_json_schema=False, on_invoke_tool=invoke, needs_approval=needs_approval)
