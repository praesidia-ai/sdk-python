"""LangGraph ToolNode managed tool; real tool IDs and state updates are injected."""
from __future__ import annotations

from typing import Any, Annotated

from .protected_tool import ManagedProtectedTool, RuntimeBinding, RuntimeCall


def langgraph_tool(managed: ManagedProtectedTool):
    """Use with ToolNode and graph state containing messages + praesidia_tools.

    The durable approval graph remains available in integrations.langgraph for
    automatic interrupt/resume. This tool returns a pending result and stores its
    cursor via Command; a host resumes the original logical call after approval.
    """
    from langchain_core.messages import ToolMessage
    from langchain_core.runnables import RunnableConfig
    from langchain_core.tools import InjectedToolCallId, StructuredTool
    from langgraph.prebuilt import InjectedState
    from langgraph.types import Command

    def invoke(body: dict[str, Any], state, tool_call_id, config):
        cursor = dict(state.get("praesidia_tools", {}))
        call = RuntimeCall("langgraph", config.get("configurable", {}).get("thread_id"), tool_call_id)
        result = managed.invoke(body, RuntimeBinding(call, cursor))
        import json
        return Command(update={"praesidia_tools": cursor,
                               "messages": [ToolMessage(content=json.dumps(result, allow_nan=False),
                                                        name=managed.name, tool_call_id=tool_call_id)]})

    invoke.__annotations__.update(state=Annotated[dict, InjectedState],
                                  tool_call_id=Annotated[str, InjectedToolCallId],
                                  config=RunnableConfig)
    return StructuredTool.from_function(invoke, name=managed.name, description=managed.description)
