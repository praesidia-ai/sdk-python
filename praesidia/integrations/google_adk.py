"""ADK managed FunctionTool; identity and approval cursor live in ToolContext."""
from __future__ import annotations

import asyncio
from typing import Any

from .protected_tool import ManagedProtectedTool, RuntimeBinding, RuntimeCall


def google_adk_tool(managed: ManagedProtectedTool):
    """Return a FunctionTool using the real session and function-call IDs.

    The Runner persists its state delta. Re-enter the same logical call after the
    backend decision; ADK confirmation booleans never authorize dispatch. This
    does not depend on ADK's experimental confirmation-storage support.
    """
    from google.adk.tools import FunctionTool, ToolContext

    async def invoke(body: dict[str, Any], tool_context) -> dict[str, Any]:
        if not isinstance(tool_context, ToolContext):
            raise ValueError("ADK ToolContext is required")
        state = tool_context.state
        # Replace the top-level value to record ADK's session state delta.
        cursor = dict(state.get("praesidia_tools", {}))
        binding = RuntimeBinding(RuntimeCall("google-adk", tool_context.session.id,
                                            tool_context.function_call_id), cursor,
                                 lambda: state.__setitem__("praesidia_tools", dict(cursor)))
        return await asyncio.to_thread(managed.invoke, body, binding)

    invoke.__name__ = managed.name
    invoke.__doc__ = managed.description
    invoke.__annotations__["tool_context"] = ToolContext
    return FunctionTool(invoke)
