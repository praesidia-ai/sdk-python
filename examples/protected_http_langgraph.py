"""Connected acceptance: run prepare, exit, then run resume in a fresh process.

PRAESIDIA_BASE_URL, PRAESIDIA_ORG_ID, PRAESIDIA_API_KEY identify the requester.
PRAESIDIA_APPROVER_TOKEN identifies a DISTINCT human session (JWT).
TARGET_PUBLIC_PIN_FILE is obtained from the independently operated target.
CHECKPOINT_DB is a durable SQLite file, THREAD_ID a unique workflow instance.
The prepare phase performs no target effect; resume approves through Praesidia,
executes once, verifies the target receipt, then records caller acknowledgment.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import httpx
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from praesidia import Praesidia
from praesidia.integrations.langgraph import protected_http_graph
from praesidia.protected_http import verify_protected_http_result
from protected_http_replay_assertion import assert_replay_rejected

parser = argparse.ArgumentParser()
parser.add_argument('phase', choices=['prepare', 'resume'])
args = parser.parse_args()
base = os.environ['PRAESIDIA_BASE_URL'].rstrip('/')
org = os.environ['PRAESIDIA_ORG_ID']
thread = os.environ['THREAD_ID']
request = {'targetId': 'acceptance-target', 'body': {'value': 'approved-test-value'}, 'description': 'Record one acceptance value at the pinned target',
           'checkpoint': {'runtime': 'langgraph', 'threadId': thread, 'nodeId': 'record-once'}}
client = Praesidia(api_key=os.environ['PRAESIDIA_API_KEY'], org_id=org, base_url=base, retry=False)
config = {'configurable': {'thread_id': thread}}
with SqliteSaver.from_conn_string(os.environ['CHECKPOINT_DB']) as saver:
    graph = protected_http_graph(client.protected_http, checkpointer=saver)
    if args.phase == 'prepare':
        state = graph.invoke({'request': request}, config)
        print(json.dumps(state['__interrupt__'][0].value))
    else:
        saved = graph.get_state(config).values
        approval_id = saved['approval']['approvalId']
        response = httpx.post(f'{base}/organizations/{org}/approvals/{approval_id}/approve',
                              headers={'Authorization': f"Bearer {os.environ['PRAESIDIA_APPROVER_TOKEN']}"},
                              json={'comment': 'Independent reviewer inspected exact target, body and commitment'}, timeout=30)
        response.raise_for_status()
        state = graph.invoke(Command(resume=True), config)
        result = state['execution']
        target = json.loads(Path(os.environ['TARGET_PUBLIC_PIN_FILE']).read_text())
        if not verify_protected_http_result(result, request, target, org):
            raise RuntimeError('Independent target receipt verification failed')
        client.protected_http.acknowledge(result)
        # API replay must fail; a lost result is recovered through GET checkpoint.
        assert_replay_rejected(
            lambda: client.protected_http.resume({k: v for k, v in {**request, 'approvalId': approval_id}.items() if k != 'description'}),
            f'/organizations/{org}/protected-actions/http/resume',
        )
        print(json.dumps({'approvalId': approval_id, 'actionId': result['actionId'], 'closure': result['closure'],
                          'targetReceiptVerified': True, 'callerAcknowledged': True, 'replayRejected': True}))
