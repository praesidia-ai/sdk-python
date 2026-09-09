"""Agno managed Function using framework-injected RunContext and FunctionCall."""
from __future__ import annotations

from typing import Any

from .protected_tool import ManagedProtectedTool, RuntimeBinding, RuntimeCall


def agno_tool(managed: ManagedProtectedTool):
    """Return a real Agno Function. Persist the actual session state and call ID.

    The native fc/run_context parameters are hidden from the model schema. This
    tool uses Praesidia approval authority; AgentOS's separate approval UI is not
    implicitly wired or claimed. Re-enter the same call after the decision.
    """
    from agno.tools import tool
    from agno.run import RunContext
    from agno.tools.function import FunctionCall

    def invoke(body: dict[str, Any], run_context, fc) -> dict[str, Any]:
        if not isinstance(run_context, RunContext) or not isinstance(fc, FunctionCall):
            raise ValueError("Agno native run and function-call contexts are required")
        if not isinstance(run_context.session_state, dict):
            raise ValueError("Agno requires persisted session state")
        state = run_context.session_state.setdefault("praesidia_tools", {})
        return managed.invoke(body, RuntimeBinding(RuntimeCall("agno", run_context.session_id, fc.call_id), state))

    invoke.__name__ = managed.name
    invoke.__doc__ = managed.description
    invoke.__annotations__.update(run_context=RunContext, fc=FunctionCall)
    function = tool(name=managed.name, description=managed.description, cache_results=False)(invoke)
    # Agno 3.0.6 interprets Any values in dict[str, Any] as nested objects.
    # This managed API accepts arbitrary JSON, so publish that exact schema.
    function.parameters = {"type": "object", "properties": {
        "body": {"type": "object", "additionalProperties": True}},
        "required": ["body"], "additionalProperties": False}
    return function
