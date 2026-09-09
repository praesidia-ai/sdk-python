"""Actual StateGraph + SQLite saver reopened between invocations. Backend is a
stateful test double; real target/approval cryptography is covered in BE acceptance."""
import pytest
pytest.importorskip('langgraph')
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from praesidia.integrations.langgraph import protected_http_graph

class Platform:
    def __init__(self):
        self.prepares = 0
        self.dispatches = 0
        self.approved = False
        self.consumed = False
    def prepare(self, request):
        self.prepares += 1
        return {'approvalId': 'approval-1', 'actionId': 'action-1', 'status': 'PENDING', 'requestCommitment': 'exact-request'}
    def checkpoint(self, approval_id):
        return {'approvalId': approval_id, 'consumedAt': '2026-09-05T00:00:00Z' if self.consumed else None, 'closure': 'SUCCEEDED' if self.consumed else None}
    def resume(self, request):
        if not self.approved: raise PermissionError('Durable human approval is missing')
        if self.consumed: raise PermissionError('Already consumed')
        self.consumed = True
        self.dispatches += 1
        return {'closure': 'SUCCEEDED', 'actionId': 'action-1'}

def request():
    return {'request': {'targetId': 'target', 'body': {'amount': '5'}, 'description': 'Credit five', 'checkpoint': {'runtime': 'langgraph', 'threadId': 'thread-1', 'nodeId': 'credit-1'}}}

def test_restart_resumes_same_durable_approval_without_repreparing(tmp_path):
    platform = Platform()
    database = str(tmp_path / 'checkpoints.sqlite')
    config = {'configurable': {'thread_id': 'thread-1'}}
    with SqliteSaver.from_conn_string(database) as saver:
        graph = protected_http_graph(platform, checkpointer=saver)
        first = graph.invoke(request(), config)
        assert first['__interrupt__'][0].value['approvalId'] == 'approval-1'
        assert platform.dispatches == 0
    platform.approved = True
    # New saver, new compiled graph: no process-local approval/checkpoint state.
    with SqliteSaver.from_conn_string(database) as saver:
        graph = protected_http_graph(platform, checkpointer=saver)
        result = graph.invoke(Command(resume=True), config)
        assert result['execution']['closure'] == 'SUCCEEDED'
    assert platform.prepares == 1
    assert platform.dispatches == 1

def test_resume_value_does_not_authorize_dispatch(tmp_path):
    platform = Platform()
    with SqliteSaver.from_conn_string(str(tmp_path / 'denied.sqlite')) as saver:
        graph = protected_http_graph(platform, checkpointer=saver)
        config = {'configurable': {'thread_id': 'thread-1'}}
        graph.invoke(request(), config)
        with pytest.raises(PermissionError, match='human approval'):
            graph.invoke(Command(resume={'approved': True, 'approverId': 'spoofed'}), config)
    assert platform.dispatches == 0

def test_rehydration_after_dispatch_observes_existing_result_instead_of_executing(tmp_path):
    platform = Platform()
    with SqliteSaver.from_conn_string(str(tmp_path / 'consumed.sqlite')) as saver:
        graph = protected_http_graph(platform, checkpointer=saver)
        config = {'configurable': {'thread_id': 'thread-1'}}
        graph.invoke(request(), config)
        platform.consumed = True
        result = graph.invoke(Command(resume=True), config)
        assert result['execution']['closure'] == 'SUCCEEDED'
    assert platform.dispatches == 0

def test_thread_binding_must_match_actual_runtime_cursor(tmp_path):
    platform = Platform()
    with SqliteSaver.from_conn_string(str(tmp_path / 'wrong.sqlite')) as saver:
        graph = protected_http_graph(platform, checkpointer=saver)
        with pytest.raises(ValueError, match='thread_id'):
            graph.invoke(request(), {'configurable': {'thread_id': 'other-thread'}})
    assert platform.prepares == 0
