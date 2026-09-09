"""Restartable LangGraph approval bridge. pip install praesidia[langgraph]."""
from __future__ import annotations
from typing import Any, TypedDict
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from ..protected_http import ProtectedHttpResource

class ProtectedHttpState(TypedDict, total=False):
    request: dict[str, Any]
    approval: dict[str, Any]
    execution: dict[str, Any]


def protected_http_graph(resource: ProtectedHttpResource, *, checkpointer: Any):
    """Compile with a caller-owned durable saver (e.g. SqliteSaver/PostgresSaver).
    Use a stable configurable.thread_id. Backend approval is authoritative: the
    interrupt resume value is only a wake-up signal and cannot authorize execution.
    A new logical action in the same thread needs a new checkpoint.nodeId.
    """
    if checkpointer is None:
        raise ValueError("A durable LangGraph checkpointer is required")

    def prepare(state: ProtectedHttpState, config: RunnableConfig) -> dict[str, Any]:
        request = dict(state["request"])
        checkpoint = dict(request["checkpoint"])
        thread_id = config.get("configurable", {}).get("thread_id")
        if not isinstance(thread_id, str) or not thread_id or checkpoint.get("threadId") != thread_id or checkpoint.get("runtime") != "langgraph":
            raise ValueError("Checkpoint must bind the actual LangGraph thread_id and runtime")
        # Backend unique checkpoint makes this safe even if the process exits
        # after prepare commits but before LangGraph saves the node result.
        return {"approval": resource.prepare(request)}

    def approval_gate(state: ProtectedHttpState) -> dict[str, Any]:
        interrupt({"kind": "praesidia_approval_required", **state["approval"]})
        return {}

    def execute(state: ProtectedHttpState) -> dict[str, Any]:
        approval_id = state["approval"]["approvalId"]
        current = resource.checkpoint(approval_id)
        if current.get("consumedAt"):
            # Process failure after dispatch cannot cause automatic re-execution.
            return {"execution": current}
        request = {k: v for k, v in state["request"].items() if k in {"targetId", "body", "checkpoint"}}
        return {"execution": resource.resume({**request, "approvalId": approval_id})}

    builder = StateGraph(ProtectedHttpState)
    builder.add_node("prepare", prepare)
    builder.add_node("approval_gate", approval_gate)
    builder.add_node("execute", execute)
    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "approval_gate")
    builder.add_edge("approval_gate", "execute")
    builder.add_edge("execute", END)
    return builder.compile(checkpointer=checkpointer)
