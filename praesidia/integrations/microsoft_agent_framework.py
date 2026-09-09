"""Agent Framework Python FunctionTool preserving its native invocation ID."""
from __future__ import annotations

import asyncio
from typing import Any

from .protected_tool import ManagedProtectedTool, RuntimeBinding, RuntimeCall


def microsoft_tool(managed: ManagedProtectedTool):
    """Return a real FunctionTool; persist AgentSession.state and session_id.

    Agent Framework passes tool_call_id to invoke separately from its function
    context. Capture that argument rather than accepting a model-provided ID.
    A native/local approval alone never authorizes backend execution.
    """
    from agent_framework import FunctionTool, FunctionInvocationContext

    async def invoke(body: dict[str, Any], ctx) -> dict[str, Any]:
        if ctx.session is None:
            raise ValueError("Agent Framework requires a persisted AgentSession")
        cursor = ctx.session.state.setdefault("praesidia_tools", {})
        call = RuntimeCall("microsoft-agent-framework", ctx.session.session_id,
                           ctx.kwargs.get("praesidia_native_call_id"))
        return await asyncio.to_thread(managed.invoke, body, RuntimeBinding(call, cursor))

    invoke.__annotations__["ctx"] = FunctionInvocationContext

    class ManagedMicrosoftTool(FunctionTool):
        async def invoke(self, *, arguments=None, context=None, tool_call_id=None,
                         skip_parsing=False, **kwargs):
            if not tool_call_id or context is None or context.session is None:
                raise ValueError("Native tool_call_id and AgentSession are required")
            actual = FunctionInvocationContext(function=self, arguments=arguments or {},
                                                session=context.session,
                                                metadata=context.metadata,
                                                kwargs={**context.kwargs, "praesidia_native_call_id": tool_call_id})
            return await super().invoke(arguments=arguments, context=actual,
                                        tool_call_id=tool_call_id, skip_parsing=skip_parsing, **kwargs)

    return ManagedMicrosoftTool(func=invoke, name=managed.name, description=managed.description)
