"""Pinned, real Hermes loader/hooks/middleware/registry; no model invocation.

Set PRAESIDIA_HERMES_SOURCE to the documented upstream checkout. No fake Hermes
classes are used. Only an owned profile and the local API contract fixture exist.
"""
import json
import os
from pathlib import Path

import pytest

from test_framework_tools import endpoint

pytestmark = pytest.mark.framework


@pytest.fixture
def hermes(endpoint, tmp_path, monkeypatch):
    source = os.getenv("PRAESIDIA_HERMES_SOURCE")
    if not source:
        if os.getenv("PRAESIDIA_REQUIRE_HERMES") == "1":
            pytest.fail("PRAESIDIA_HERMES_SOURCE must identify the pinned actual checkout")
        pytest.skip("Optional pinned Hermes checkout not configured")
    import subprocess
    actual = subprocess.check_output(["git", "-C", source, "rev-parse", "HEAD"], text=True).strip()
    assert actual == "0390ace8179f4cf75bd3941e590dd74e638672b6"
    monkeypatch.syspath_prepend(source)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("PRAESIDIA_API_KEY", "pk_test_runtime_fixture")
    monkeypatch.setenv("PRAESIDIA_ORG_ID", "runtime-org")
    monkeypatch.setenv("PRAESIDIA_API_URL", endpoint[1]["baseUrl"])
    (tmp_path / "config.yaml").write_text("""plugins:
  enabled: [praesidia]
  entries:
    praesidia:
      enabled: true
      settings:
        target_id: local-record
        strict_tools: true
""")
    from hermes_cli.plugins import get_plugin_manager, discover_entrypoint_manifests
    manifest = next(m for m in discover_entrypoint_manifests() if m.name == "praesidia")
    assert manifest.source == "entrypoint" and manifest.path == "praesidia_hermes"
    manager = get_plugin_manager()
    manager.discover_and_load()
    loaded = manager._plugins["praesidia"]
    assert loaded.enabled, loaded.error
    assert loaded.tools_registered == ["praesidia_protected_http"]
    assert loaded.hooks_registered == ["pre_tool_call"]
    assert loaded.middleware_registered == ["tool_execution"]
    try:
        yield manager, endpoint[1], tmp_path
    finally:
        manager.unload()


def test_actual_hermes_dispatch_and_durable_plugin_state(hermes):
    manager, backend, profile = hermes
    from model_tools import handle_function_call
    def invoke():
        result = handle_function_call("praesidia_protected_http", {"body": {"amount": 5}},
            task_id="host-task", session_id="hermes-session", tool_call_id="native-call")
        return json.loads(result) if isinstance(result, str) else result
    pending = invoke()
    assert pending["disposition"] == "approval_required"
    assert backend["dispatches"] == 0
    files = list(profile.rglob("*.json"))
    cursor_files = [p for p in files if pending["approvalId"] in p.read_text()]
    assert len(cursor_files) == 1
    assert cursor_files[0].stat().st_mode & 0o777 == 0o600
    # Actual unload/reload reconstructs the plugin; no Python cursor survives.
    manager.discover_and_load(force=True)
    backend["approved"] = True
    result = invoke()
    assert result["closure"] == "SUCCEEDED"
    assert invoke()["actionId"] == result["actionId"]
    assert backend["dispatches"] == 1
    assert backend["request"]["checkpoint"]["runtime"] == "hermes"


def test_actual_hermes_strict_hooks_block_unrelated_and_bad_native_context(hermes):
    _, backend, _ = hermes
    from hermes_cli.plugins import _dispatch_pre_tool_call_hooks
    from hermes_cli.middleware import run_tool_execution_middleware
    from tools.registry import registry
    from praesidia_hermes import TOOL_NAME
    calls = []
    block, _ = _dispatch_pre_tool_call_hooks("shell_exec", {}, session_id="s", tool_call_id="c")
    assert "only the Praesidia" in block
    result = run_tool_execution_middleware("shell_exec", {}, lambda args: calls.append(args),
                                           session_id="s", tool_call_id="c")
    assert json.loads(result).get("error") and not calls
    for args, session, call in [({"body": {}}, None, "c"), ({"body": {}}, "s", None),
                                 ({"body": {}, "resume": True}, "s", "c")]:
        block, _ = _dispatch_pre_tool_call_hooks(TOOL_NAME, args, session_id=session, tool_call_id=call)
        assert block
        result = run_tool_execution_middleware(TOOL_NAME, args, lambda a: calls.append(a),
                                               session_id=session, tool_call_id=call)
        assert json.loads(result).get("error") and not calls
    # Native host accidentally bypasses execution middleware: handler refuses it.
    result = registry.dispatch(TOOL_NAME, {"body": {}}, session_id="s")
    if isinstance(result, str):
        result = json.loads(result)
    assert "middleware" in result["error"]
    assert backend["requests"] == []


def test_actual_hermes_changed_body_and_failed_http_never_run_local_effect(hermes):
    _, backend, _ = hermes
    from model_tools import handle_function_call
    def invoke(body):
        value = handle_function_call("praesidia_protected_http", {"body": body},
                                      session_id="s", tool_call_id="c")
        return json.loads(value) if isinstance(value, str) else value
    assert invoke({"amount": 5})["disposition"] == "approval_required"
    backend["approved"] = True
    assert "failed closed" in invoke({"amount": 9})["error"]
    assert backend["dispatches"] == 0
