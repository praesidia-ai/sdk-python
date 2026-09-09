"""Restartable real-framework invocation against a running Praesidia API.

This host fixture issues a stable native call ID; no LLM/provider is involved.
Run once to prepare, approve through the independent backend API/UI, then rerun
the identical command. The state file is host-owned and must never be model input.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Annotated, TypedDict

from praesidia import Praesidia
from praesidia.integrations.protected_tool import (
    ManagedProtectedTool, RuntimeBinding, RuntimeCall,
)
from praesidia.integrations import FileRuntimeAttemptStore

RUNTIMES = ("crewai", "openai-agents", "google-adk", "microsoft-agent-framework", "agno", "langgraph", "hermes")


def atomic_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".praesidia-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(state, stream, allow_nan=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


async def invoke_framework(runtime, managed, body, thread_id, call_id, cursor, storage):
    """Use actual framework tool execution and injected native contexts."""
    args = {"body": body}
    if runtime == "hermes":
        source = os.environ.get("PRAESIDIA_HERMES_SOURCE")
        if source:
            import subprocess
            revision = subprocess.check_output(["git", "-C", source, "rev-parse", "HEAD"], text=True).strip()
            if revision != "0390ace8179f4cf75bd3941e590dd74e638672b6":
                raise ValueError("Hermes acceptance requires the documented pinned source")
            sys.path.insert(0, source)
        profile = storage / "hermes-profile"
        profile.mkdir(parents=True, exist_ok=True)
        os.environ["HERMES_HOME"] = str(profile)
        config = {"plugins": {"enabled": ["praesidia"], "entries": {"praesidia": {
            "enabled": True, "settings": {"target_id": managed.target_id, "strict_tools": True}}}}}
        path = profile / "config.yaml"
        if path.exists() and json.loads(path.read_text()) != config:
            raise ValueError("Persisted Hermes plugin configuration changed")
        if not path.exists():
            atomic_state(path, config)
        from hermes_cli.plugins import get_plugin_manager
        manager = get_plugin_manager()
        manager.discover_and_load()
        plugin = manager._plugins.get("praesidia")
        if plugin is None or not plugin.enabled:
            raise RuntimeError("Hermes Praesidia plugin did not load")
        try:
            from model_tools import handle_function_call
            from praesidia_hermes import TOOL_NAME
            result = handle_function_call(TOOL_NAME, args, session_id=thread_id, tool_call_id=call_id)
            parsed = json.loads(result) if isinstance(result, str) else result
            if parsed.get("kind") != "praesidia.protected-tool.v1":
                raise RuntimeError("Hermes managed tool failed closed")
            return parsed
        finally:
            manager.unload()
    if runtime == "openai-agents":
        from agents.tool import invoke_function_tool
        from agents.tool_context import ToolContext
        from praesidia.integrations.openai_agents import openai_tool
        host = {"praesidia_thread_id": thread_id, "praesidia_tools": cursor}
        raw = json.dumps(args, allow_nan=False)
        context = ToolContext(host, tool_name=managed.name, tool_call_id=call_id, tool_arguments=raw)
        return json.loads(await invoke_function_tool(function_tool=openai_tool(managed), context=context, arguments=raw))
    if runtime == "google-adk":
        from google.adk.agents.invocation_context import InvocationContext
        from google.adk.sessions import InMemorySessionService
        from google.adk.tools import ToolContext
        from praesidia.integrations.google_adk import google_adk_tool
        sessions = InMemorySessionService()
        session = await sessions.create_session(app_name="praesidia_acceptance", user_id="host-fixture",
                                                session_id=thread_id, state={"praesidia_tools": cursor})
        context = ToolContext(InvocationContext(session_service=sessions, invocation_id=call_id,
                                                session=session), function_call_id=call_id)
        return await google_adk_tool(managed).run_async(args=args, tool_context=context)
    if runtime == "microsoft-agent-framework":
        from agent_framework import AgentSession, FunctionInvocationContext
        from praesidia.integrations.microsoft_agent_framework import microsoft_tool
        session = AgentSession(session_id=thread_id)
        session.state["praesidia_tools"] = cursor
        tool = microsoft_tool(managed)
        result = await tool.invoke(arguments=args, tool_call_id=call_id,
                                   context=FunctionInvocationContext(function=tool, arguments=args, session=session))
        return json.loads(result[0].text)
    if runtime == "agno":
        from agno.tools.function import FunctionCall
        from agno.run import RunContext
        from praesidia.integrations.agno import agno_tool
        tool = agno_tool(managed)
        # Same injection seam used by Agno's tool binding during an actual run.
        tool._run_context = RunContext(run_id=call_id, session_id=thread_id,
                                       session_state={"praesidia_tools": cursor})
        call = FunctionCall(function=tool, arguments=args, call_id=call_id)
        result = call.execute()
        if result.status != "success":
            raise RuntimeError("Agno managed tool invocation failed")
        return result.result
    if runtime == "crewai":
        os.environ.setdefault("CREWAI_STORAGE_DIR", str(storage))
        os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
        os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
        # CrewAI imports its auth manager even for a tool-only run. Redirect only
        # that fixture-local storage seam; never read the operator's real token.
        from crewai_core.token_manager import TokenManager
        TokenManager._get_secure_storage_path = staticmethod(lambda: storage)
        from crewai.flow.flow import Flow
        from praesidia.integrations.crewai import crewai_tool
        flow = Flow()
        flow.state.update(id=thread_id, praesidia_tools=cursor)
        tool = crewai_tool(managed, binding=lambda: RuntimeBinding(
            RuntimeCall("crewai", flow.state["id"], call_id), flow.state["praesidia_tools"]))
        return tool.run(**args)
    if runtime == "langgraph":
        from langchain_core.messages import AIMessage
        from langgraph.graph import StateGraph, START, END
        from langgraph.graph.message import add_messages
        from langgraph.prebuilt import ToolNode
        from praesidia.integrations.langgraph_tools import langgraph_tool
        # Build the annotations with the actual imported reducer, not its name.
        State = TypedDict("State", {"messages": Annotated[list, add_messages], "praesidia_tools": dict})
        builder = StateGraph(State)
        builder.add_node("tools", ToolNode([langgraph_tool(managed)], handle_tool_errors=False))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        message = AIMessage(content="", tool_calls=[{"name": managed.name, "args": args, "id": call_id}])
        result = builder.compile().invoke({"messages": [message], "praesidia_tools": cursor},
                                           {"configurable": {"thread_id": thread_id}})
        return json.loads(result["messages"][-1].content)
    raise ValueError("Unsupported native framework")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, choices=RUNTIMES)
    parser.add_argument("--state-file", required=True, type=Path)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--call-id", required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--tool-name", default="praesidia_managed_write")
    parser.add_argument("--body-file", required=True, type=Path)
    parser.add_argument("--expect", choices=("approval_required", "recorded_outcome", "inspection_required", "not_dispatched"))
    opts = parser.parse_args()
    if not opts.state_file.is_absolute():
        parser.error("--state-file must be an absolute host-owned path")
    body = json.loads(opts.body_file.read_text())
    identity = {"runtime": opts.runtime, "threadId": opts.thread_id, "callId": opts.call_id}
    document = json.loads(opts.state_file.read_text()) if opts.state_file.exists() else {"identity": identity, "cursor": {}}
    if document.get("identity") != identity or not isinstance(document.get("cursor"), dict):
        raise ValueError("Persisted framework identity changed")
    atomic_state(opts.state_file, document)
    client = Praesidia(api_key=os.environ["PRAESIDIA_API_KEY"], org_id=os.environ["PRAESIDIA_ORG_ID"],
                       base_url=os.environ["PRAESIDIA_API_URL"])

    class PersistedTool(ManagedProtectedTool):
        def _operate(self, body, binding, *, dispatch):
            def persist():
                document["cursor"] = dict(binding.state)
                atomic_state(opts.state_file, document)
            # Commit approval/attempt cursor before dispatch even when a native
            # framework would normally flush its state only after tool return.
            actual = RuntimeBinding(binding.call, binding.state, persist=persist)
            return super()._operate(body, actual, dispatch=dispatch)

    managed = PersistedTool(client.protected_http, target_id=opts.target_id, name=opts.tool_name,
                            description="Execute the exact independently reviewed managed HTTP request",
                            attempt_store=FileRuntimeAttemptStore(opts.state_file.with_suffix(opts.state_file.suffix + ".attempts")))
    os.environ.setdefault("OTEL_SDK_DISABLED", "true")
    with redirect_stdout(sys.stderr):
        result = asyncio.run(invoke_framework(opts.runtime, managed, body, opts.thread_id, opts.call_id,
                                              document["cursor"], opts.state_file.parent))
    print(json.dumps(result, allow_nan=False, sort_keys=True))
    if opts.expect and result["disposition"] != opts.expect:
        raise SystemExit("Unexpected authoritative protected-tool disposition")


if __name__ == "__main__":
    main()
