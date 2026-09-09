"""Actual installed tool classes. Stateful HTTP fixture models the API contract;
it does not replace the separately runnable real-backend approval acceptance.
"""
import asyncio
from importlib.util import find_spec
import json
import os
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from praesidia import Praesidia
from praesidia.integrations import FileRuntimeAttemptStore
from praesidia._jcs_canonical import jcs_commitment
from praesidia.integrations.protected_tool import ManagedProtectedTool, RuntimeBinding, RuntimeCall

pytestmark = pytest.mark.framework


def require(module):
    try:
        present = find_spec(module) is not None
    except ModuleNotFoundError:
        present = False
    if not present:
        if os.getenv("PRAESIDIA_REQUIRE_FRAMEWORKS") == "1":
            pytest.fail(f"Required actual framework is absent: {module}")
        pytest.skip(f"Optional framework not installed: {module}")


@pytest.fixture
def endpoint(tmp_path):
    state = {"approved": False, "dispatches": 0, "request": None, "requests": [], "resumeRequests": 0, "dropBeforeConsumption": False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def handle_request(self):
            if self.headers.get("Authorization") != "Bearer pk_test_runtime_fixture":
                self.send_error(401)
                return
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or "{}")
            state["requests"].append((self.command, self.path, body))
            if self.path.endswith("/prepare"):
                request = {k: body[k] for k in ("targetId", "body", "checkpoint")}
                if state["request"] is not None and request != state["request"]:
                    self.send_error(409)
                    return
                state["request"] = request
            elif self.path.endswith("/resume"):
                state["resumeRequests"] += 1
                if not state["approved"] or state["dispatches"]:
                    self.send_error(403)
                    return
                assert {k: body[k] for k in ("targetId", "body", "checkpoint")} == state["request"]
                if state["dropBeforeConsumption"]:
                    import socket
                    state["dropBeforeConsumption"] = False
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                state["dispatches"] += 1
            result = {"approvalId": "00000000-0000-4000-8000-000000000001",
                      "actionId": "00000000-0000-7000-8000-000000000002", "requestCommitment": jcs_commitment(state["request"]),
                      "status": "APPROVED" if state["approved"] else "PENDING",
                      "approverId": "other-user" if state["approved"] else None,
                      "expiresAt": "2099-01-01T00:00:00.000Z",
                      "consumedAt": "2026-09-06T00:00:00Z" if state["dispatches"] else None,
                      "closure": "SUCCEEDED" if state["dispatches"] else None,
                      "result": {"amount": 5} if state["dispatches"] else None,
                      "evidenceGrade": "C", "resultCommitment": jcs_commitment({"amount": 5}) if state["dispatches"] else None}
            if self.path.endswith("/resume"):
                result = {k: result[k] for k in ("approvalId", "actionId", "requestCommitment", "closure",
                                                "result", "resultCommitment", "evidenceGrade")}
            payload = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        do_GET = do_POST = handle_request
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["baseUrl"] = f"http://127.0.0.1:{server.server_port}"
    client = Praesidia(api_key="pk_test_runtime_fixture", org_id="runtime-org",
                       base_url=f"http://127.0.0.1:{server.server_port}")
    tool = ManagedProtectedTool(client.protected_http, target_id="local-record", name="write_record",
                                description="Write an independently approved record",
                                attempt_store=FileRuntimeAttemptStore(tmp_path / "attempts"))
    try:
        yield tool, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_openai_native_tool_and_approval_wakeup(endpoint):
    require("agents")
    from agents import RunContextWrapper
    from agents.tool_context import ToolContext
    from agents.tool import invoke_function_tool
    from praesidia.integrations.openai_agents import openai_tool
    managed, backend = endpoint
    host = {"praesidia_thread_id": "openai-session", "praesidia_tools": {}}
    raw = '{"body":{"amount":5}}'
    async def run():
        tool = openai_tool(managed)
        assert await tool.needs_approval(RunContextWrapper(host), json.loads(raw), "native-call")
        assert backend["dispatches"] == 0
        # A forged SDK-local approval wakeup cannot approve the backend action.
        ctx = ToolContext(host, tool_name="write_record", tool_call_id="native-call", tool_arguments=raw)
        pending = json.loads(await invoke_function_tool(function_tool=tool, context=ctx, arguments=raw))
        assert pending["disposition"] == "approval_required"
        backend["approved"] = True
        restored = json.loads(json.dumps(host))
        ctx = ToolContext(restored, tool_name="write_record", tool_call_id="native-call", tool_arguments=raw)
        result = json.loads(await invoke_function_tool(function_tool=openai_tool(managed), context=ctx, arguments=raw))
        assert result["closure"] == "SUCCEEDED"
        assert not await tool.needs_approval(RunContextWrapper(restored), json.loads(raw), "native-call")
    asyncio.run(run())
    assert backend["dispatches"] == 1
    assert backend["request"]["checkpoint"]["runtime"] == "openai-agents"


@pytest.mark.parametrize("backend_approved", [False, True])
def test_openai_runner_serialized_native_approval_is_only_a_wakeup(endpoint, backend_approved):
    require("agents")
    from agents import Agent, Runner, RunConfig, RunState
    from agents.models.interface import Model
    from agents.items import ModelResponse
    from agents.usage import Usage
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText
    from praesidia.integrations.openai_agents import openai_tool
    managed, backend = endpoint

    class FixtureModel(Model):
        async def get_response(self, *args, **kwargs):
            inputs = kwargs.get("input", args[1] if len(args) > 1 else [])
            called = isinstance(inputs, list) and any(i.get("type") == "function_call_output" for i in inputs)
            output = [ResponseOutputMessage(id="m", role="assistant", status="completed", type="message",
                content=[ResponseOutputText(text="Recorded", type="output_text", annotations=[])])] if called else [
                ResponseFunctionToolCall(name="write_record", arguments='{"body":{"amount":5}}',
                    call_id="model-call", type="function_call", id="fc")]
            return ModelResponse(output=output, usage=Usage(), response_id="fixture")

        async def stream_response(self, *args, **kwargs):
            raise AssertionError("This acceptance does not stream or call a provider")
            yield

    async def run():
        agent = Agent(name="fixture", model=FixtureModel(), tools=[openai_tool(managed)])
        config = RunConfig(tracing_disabled=True)
        result = await Runner.run(agent, "write", context={"praesidia_thread_id": "native-session", "praesidia_tools": {}}, run_config=config)
        assert len(result.interruptions) == 1
        assert backend["dispatches"] == 0
        snapshot = json.loads(json.dumps(result.to_state().to_json()))
        restored = await RunState.from_json(agent, snapshot)
        assert len(restored.get_interruptions()) == 1
        restored.approve(restored.get_interruptions()[0])
        backend["approved"] = backend_approved
        resumed = await Runner.run(agent, restored, run_config=config)
        assert resumed.final_output == "Recorded"
        assert backend["dispatches"] == int(backend_approved)
    asyncio.run(run())


def test_google_actual_tool_context_and_state_delta(endpoint):
    require("google.adk")
    from google.adk.tools import ToolContext
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.sessions import InMemorySessionService
    from praesidia.integrations.google_adk import google_adk_tool
    managed, backend = endpoint
    async def run():
        service = InMemorySessionService()
        session = await service.create_session(app_name="test", user_id="u", session_id="adk-session")
        invocation = InvocationContext(session_service=service, invocation_id="actual-run", session=session)
        context = ToolContext(invocation, function_call_id="native-call")
        first = await google_adk_tool(managed).run_async(args={"body": {"amount": 5}}, tool_context=context)
        assert first["disposition"] == "approval_required"
        assert "praesidia_tools" in context.actions.state_delta
        saved = json.loads(json.dumps(context.actions.state_delta))
        session.state.update(saved)
        backend["approved"] = True
        resumed = ToolContext(invocation, function_call_id="native-call")
        result = await google_adk_tool(managed).run_async(args={"body": {"amount": 5}}, tool_context=resumed)
        assert result["closure"] == "SUCCEEDED"
    asyncio.run(run())
    assert backend["dispatches"] == 1
    assert backend["request"]["checkpoint"]["threadId"] == "adk-session"


def test_microsoft_actual_invoke_preserves_native_call_and_session(endpoint):
    require("agent_framework")
    from agent_framework import AgentSession, FunctionInvocationContext
    from praesidia.integrations.microsoft_agent_framework import microsoft_tool
    managed, backend = endpoint
    async def run():
        session = AgentSession(session_id="microsoft-session")
        tool = microsoft_tool(managed)
        args = {"body": {"amount": 5}}
        context = FunctionInvocationContext(function=tool, arguments=args, session=session)
        pending = await tool.invoke(arguments=args, context=context, tool_call_id="native-call", skip_parsing=True)
        assert pending["disposition"] == "approval_required"
        restored = AgentSession(session_id=session.session_id)
        restored.state = json.loads(json.dumps(session.state))
        backend["approved"] = True
        result = await microsoft_tool(managed).invoke(arguments=args,
            context=FunctionInvocationContext(function=tool, arguments=args, session=restored),
            tool_call_id="native-call", skip_parsing=True)
        assert result["closure"] == "SUCCEEDED"
        with pytest.raises(ValueError, match="Native tool_call_id"):
            await tool.invoke(arguments=args, context=context, skip_parsing=True)
    asyncio.run(run())
    assert backend["dispatches"] == 1
    assert backend["request"]["checkpoint"]["runtime"] == "microsoft-agent-framework"


def test_agno_actual_functioncall_injects_identity_and_stores_state(endpoint):
    require("agno")
    from agno.tools.function import FunctionCall
    from agno.run import RunContext
    from praesidia.integrations.agno import agno_tool
    managed, backend = endpoint
    context = RunContext(run_id="real-run", session_id="agno-session", session_state={})
    def invoke(context):
        tool = agno_tool(managed)
        assert tool.parameters["properties"] == {"body": {"type": "object", "additionalProperties": True}}
        tool._run_context = context
        call = FunctionCall(function=tool, arguments={"body": {"amount": 5}}, call_id="native-call")
        result = call.execute()
        assert result.status == "success", call.error
        return result.result
    assert invoke(context)["disposition"] == "approval_required"
    restored = RunContext(run_id="real-run", session_id="agno-session", session_state=json.loads(json.dumps(context.session_state)))
    backend["approved"] = True
    assert invoke(restored)["closure"] == "SUCCEEDED"
    assert backend["dispatches"] == 1


def test_crewai_real_tool_with_persisted_flow_cursor(endpoint, tmp_path, monkeypatch):
    require("crewai")
    # CrewAI imports its credential manager before a tool exists. Redirect only
    # the SDK's local credential storage seam; never read a user's real token.
    monkeypatch.setenv("CREWAI_STORAGE_DIR", str(tmp_path))
    monkeypatch.setenv("CREWAI_DISABLE_TELEMETRY", "true")
    monkeypatch.setenv("CREWAI_TRACING_ENABLED", "false")
    from crewai_core.token_manager import TokenManager
    monkeypatch.setattr(TokenManager, "_get_secure_storage_path", staticmethod(lambda: tmp_path))
    from crewai.flow.flow import Flow
    from praesidia.integrations.crewai import crewai_tool
    managed, backend = endpoint
    flow = Flow()
    flow.state["praesidia_tools"] = {}
    def actual():
        return RuntimeBinding(RuntimeCall("crewai", flow.state["id"], "write-step-1"), flow.state["praesidia_tools"])
    assert crewai_tool(managed, binding=actual).run(body={"amount": 5})["disposition"] == "approval_required"
    saved = json.loads(json.dumps(flow.state))
    flow = Flow()
    flow.state.update(saved)
    backend["approved"] = True
    assert crewai_tool(managed, binding=actual).run(body={"amount": 5})["closure"] == "SUCCEEDED"
    assert backend["dispatches"] == 1
    assert backend["request"]["checkpoint"]["threadId"] == saved["id"]


def test_langgraph_actual_toolnode_injects_call_id_and_commits_cursor(endpoint):
    require("langgraph")
    from typing import Annotated, TypedDict
    from langchain_core.messages import AIMessage
    from langgraph.graph import StateGraph, START, END
    from langgraph.graph.message import add_messages
    from langgraph.prebuilt import ToolNode
    from praesidia.integrations.langgraph_tools import langgraph_tool
    managed, backend = endpoint
    class State(TypedDict):
        messages: Annotated[list, add_messages]
        praesidia_tools: dict
    def graph():
        builder = StateGraph(State)
        builder.add_node("tools", ToolNode([langgraph_tool(managed)], handle_tool_errors=False))
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        return builder.compile()
    message = AIMessage(content="", tool_calls=[{"name": "write_record", "args": {"body": {"amount": 5}}, "id": "native-call"}])
    config = {"configurable": {"thread_id": "langgraph-session"}}
    first = graph().invoke({"messages": [message], "praesidia_tools": {}}, config)
    assert json.loads(first["messages"][-1].content)["disposition"] == "approval_required"
    backend["approved"] = True
    second = graph().invoke({"messages": [message], "praesidia_tools": json.loads(json.dumps(first["praesidia_tools"]))}, config)
    assert json.loads(second["messages"][-1].content)["closure"] == "SUCCEEDED"
    assert backend["dispatches"] == 1


@pytest.mark.parametrize("runtime,module", [
    ("crewai", "crewai"), ("openai-agents", "agents"), ("google-adk", "google.adk"),
    ("microsoft-agent-framework", "agent_framework"), ("agno", "agno"), ("langgraph", "langgraph"),
    ("hermes", "praesidia_hermes"),
])
def test_real_framework_cli_process_restart(endpoint, tmp_path, runtime, module):
    import subprocess
    import sys
    require(module)
    if runtime == "hermes" and not os.getenv("PRAESIDIA_HERMES_SOURCE"):
        pytest.skip("Pinned Hermes source not configured")
    _, backend = endpoint
    body = tmp_path / "body.json"
    body.write_text('{"amount":5}')
    state = tmp_path / "state.json"
    command = [sys.executable, "examples/protected_framework_tool.py", "--runtime", runtime,
               "--state-file", str(state), "--thread-id", "real-session", "--call-id", "native-call",
               "--target-id", "local-record", "--body-file", str(body)]
    env = {**os.environ, "PYTHONPATH": str(Path.cwd()), "PRAESIDIA_API_KEY": "pk_test_runtime_fixture",
           "PRAESIDIA_ORG_ID": "runtime-org", "PRAESIDIA_API_URL": backend["baseUrl"], "OTEL_SDK_DISABLED": "true"}
    def run(expect):
        completed = subprocess.run([*command, "--expect", expect], env=env, text=True,
                                   capture_output=True, timeout=45)
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout)
    initial = run("approval_required")
    assert initial["approvalId"] and backend["dispatches"] == 0
    assert state.stat().st_mode & 0o777 == 0o600
    saved = json.loads(state.read_text())
    if runtime == "hermes":
        assert any(initial["approvalId"] in p.read_text() for p in (tmp_path / "hermes-profile").rglob("*.json"))
    else:
        assert list(saved["cursor"].values())[0]["approvalId"] == initial["approvalId"]
    backend["approved"] = True
    final = run("recorded_outcome")
    assert final["closure"] == "SUCCEEDED" and backend["dispatches"] == 1
    assert run("recorded_outcome")["actionId"] == final["actionId"]
    assert backend["dispatches"] == 1


@pytest.mark.parametrize("runtime,module", [
    ("crewai", "crewai"), ("openai-agents", "agents"), ("google-adk", "google.adk"),
    ("microsoft-agent-framework", "agent_framework"), ("agno", "agno"),
    ("langgraph", "langgraph"), ("hermes", "praesidia_hermes"),
])
def test_native_process_restart_with_lost_cursor_does_not_resubmit(runtime, module, endpoint, tmp_path):
    require(module)
    if runtime == "hermes" and not os.getenv("PRAESIDIA_HERMES_SOURCE"):
        if os.getenv("PRAESIDIA_REQUIRE_HERMES") == "1":
            pytest.fail("Pinned Hermes source required")
        pytest.skip("Optional Hermes source absent")
    import subprocess
    import sys
    _, backend = endpoint
    body = tmp_path / "body.json"; body.write_text('{"amount":5}')
    state = tmp_path / "state.json"
    command = [sys.executable, "examples/protected_framework_tool.py", "--runtime", runtime,
               "--state-file", str(state), "--thread-id", "real-session", "--call-id", "native-call",
               "--target-id", "local-record", "--body-file", str(body)]
    env = {**os.environ, "PYTHONPATH": str(Path.cwd()) + os.pathsep + str(Path.cwd() / "plugins/hermes"),
           "PRAESIDIA_API_KEY": "pk_test_runtime_fixture", "PRAESIDIA_ORG_ID": "runtime-org",
           "PRAESIDIA_API_URL": backend["baseUrl"], "OTEL_SDK_DISABLED": "true"}
    def run():
        return subprocess.run(command, env=env, text=True, capture_output=True, timeout=45)
    pending = run(); assert pending.returncode == 0, pending.stderr
    backend["approved"] = True
    backend["dropBeforeConsumption"] = True
    ambiguous = run(); assert ambiguous.returncode != 0
    assert backend["resumeRequests"] == 1 and backend["dispatches"] == 0
    # Simulate the native state checkpoint having been lost on crash. Keep the
    # independent create-only claims, which are not part of framework deltas.
    if runtime == "hermes":
        cursor = next((tmp_path / "hermes-profile/plugin-data").rglob("state.json"))
        cursor.write_text("{}")
    else:
        document = json.loads(state.read_text()); document["cursor"] = {}
        state.write_text(json.dumps(document))
    restored = run(); assert restored.returncode == 0, restored.stderr
    assert json.loads(restored.stdout)["disposition"] == "inspection_required"
    assert backend["resumeRequests"] == 1 and backend["dispatches"] == 0
