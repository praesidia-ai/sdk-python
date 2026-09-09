import copy
import json
from pathlib import Path
import httpx
import pytest
import respx
from praesidia import Praesidia
from praesidia.protected_http import verify_protected_http_result, http_target_key_fingerprint
from praesidia._jcs_canonical import jcs_commitment

FIXTURE = json.loads((Path(__file__).parents[1] / 'test-fixtures/http-receipt-v1.json').read_text())

def test_shared_signed_receipt_and_exact_commitment():
    assert jcs_commitment(FIXTURE['envelope']) == FIXTURE['response']['requestCommitment']
    assert http_target_key_fingerprint(FIXTURE['target']['publicKeyPem']) == FIXTURE['envelope']['targetKeyFingerprint']
    assert verify_protected_http_result(FIXTURE['response'], FIXTURE['request'], FIXTURE['target'], FIXTURE['organizationId'])

@pytest.mark.parametrize('field', ['body', 'result', 'identity', 'signature'])
def test_independent_verification_rejects_substitution(field):
    data = copy.deepcopy(FIXTURE)
    if field == 'body': data['request']['body'] = {'amount': '999.00'}
    if field == 'result': data['response']['result'] = {'applied': '999.00'}
    if field == 'identity': data['target']['targetId'] = 'attacker'
    if field == 'signature': data['response']['receipt']['signature'] = 'A' * 86 + '=='
    assert not verify_protected_http_result(data['response'], data['request'], data['target'], data['organizationId'])

@respx.mock
def test_resource_routes_and_acknowledges_observed_bytes():
    client = Praesidia(api_key='pk_test', org_id=FIXTURE['organizationId'], base_url='https://api.example')
    base = f"https://api.example/organizations/{FIXTURE['organizationId']}/protected-actions/http"
    routes = [respx.post(f'{base}/prepare'), respx.get(f"{base}/checkpoints/{FIXTURE['response']['approvalId']}"), respx.post(f'{base}/resume'), respx.post(f'{base}/acknowledge'), respx.post(f"{base}/checkpoints/{FIXTURE['response']['approvalId']}/revoke")]
    for route in routes: route.mock(return_value=httpx.Response(200, json=FIXTURE['response']))
    client.protected_http.prepare({**FIXTURE['request'], 'description': 'Review me'})
    client.protected_http.checkpoint(FIXTURE['response']['approvalId'])
    client.protected_http.resume({**FIXTURE['request'], 'approvalId': FIXTURE['response']['approvalId']})
    client.protected_http.acknowledge(FIXTURE['response'])
    client.protected_http.revoke(FIXTURE['response']['approvalId'])
    assert all(route.call_count == 1 for route in routes)
    assert json.loads(routes[-2].calls[0].request.content) == {'approvalId': FIXTURE['response']['approvalId'], 'resultCommitment': FIXTURE['response']['resultCommitment']}

@respx.mock
def test_resume_is_never_transparently_retried_after_lost_response():
    client = Praesidia(api_key='pk_test', org_id=FIXTURE['organizationId'], base_url='https://api.example')
    route = respx.post(f"https://api.example/organizations/{FIXTURE['organizationId']}/protected-actions/http/resume").mock(side_effect=httpx.ReadError('response lost'))
    with pytest.raises(Exception): client.protected_http.resume({**FIXTURE['request'], 'approvalId': FIXTURE['response']['approvalId']})
    assert route.call_count == 1

@respx.mock
def test_installation_binding_is_exact_immutable_and_sent_on_prepare_and_resume(monkeypatch):
    installation_id = '12345678-1234-4234-8234-123456789abc'
    monkeypatch.setenv('PRAESIDIA_RUNTIME_INSTALLATION_ID', installation_id)
    client = Praesidia(api_key='pk_test', org_id=FIXTURE['organizationId'], base_url='https://api.example')
    base = f"https://api.example/organizations/{FIXTURE['organizationId']}/protected-actions/http"
    routes = [respx.post(f'{base}/prepare'), respx.post(f'{base}/resume')]
    for route in routes: route.mock(return_value=httpx.Response(200, json=FIXTURE['response']))
    client.protected_http.prepare({**FIXTURE['request'], 'description': 'Review'})
    client.protected_http.resume({**FIXTURE['request'], 'approvalId': FIXTURE['response']['approvalId']})
    for route in routes: assert json.loads(route.calls[0].request.content)['checkpoint']['installationId'] == installation_id
    assert 'installationId' not in FIXTURE['request']['checkpoint']
    changed = {**FIXTURE['request'], 'checkpoint': {**FIXTURE['request']['checkpoint'], 'installationId': 'aaaaaaaa-1234-4234-8234-123456789abc'}}
    with pytest.raises(ValueError, match='conflicts'): client.protected_http.prepare(changed)
    assert all(route.call_count == 1 for route in routes)
    with pytest.raises(ValueError, match='UUID'): Praesidia(api_key='pk_test', org_id=FIXTURE['organizationId'], runtime_installation_id='../other')
