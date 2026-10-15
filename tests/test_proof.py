import json
import os
from pathlib import Path

import pytest
import respx

from praesidia import ForbiddenError, Praesidia, ResponseTooLargeError
from praesidia.proof import PROTECTED_ACTION_STATES

BASE = "https://api.test/organizations/org-1"


def client():
    return Praesidia(api_key="pk_personal", org_id="org-1", base_url="https://api.test", retry=False)


@respx.mock
def test_list_preserves_envelope_and_exact_backend_query():
    response = {"data": [{"actionId": "action1", "closure": "SUCCEEDED", "evidenceGrade": "C"}], "total": 102, "meta": {"page": 2, "limit": 10, "total": 102, "totalPages": 11, "hasNextPage": True, "hasPrevPage": True}}
    route = respx.get(f"{BASE}/protected-actions").respond(json=response)
    assert client().proof.list(agent_id="agent-id",task_id="task-id",chain_id="chain-id & review",state="OUTCOME_UNKNOWN",closure="OUTCOME_UNKNOWN",from_date="2026-09-01T00:00:00+03:00",to_date="2026-09-02T00:00:00+03:00",page=2,limit=10) == response
    request = route.calls.last.request
    assert dict(request.url.params) == {"agentId":"agent-id","taskId":"task-id","chainId":"chain-id & review","state":"OUTCOME_UNKNOWN","closure":"OUTCOME_UNKNOWN","from":"2026-09-01T00:00:00+03:00","to":"2026-09-02T00:00:00+03:00","page":"2","limit":"10"}
    assert request.headers["Authorization"] == "Bearer pk_personal"


@respx.mock
def test_detail_events_scope_and_coverage_preserve_evidence_without_verifying():
    detail = {"actionId":"action1","closure":"SUCCEEDED","verificationStatus":"INCOMPLETE","evidenceGrade":None}
    # PRA-146: a format-2 row carries every signed-preimage field; all must survive unchanged.
    events = [{"actionSeq":"90071992547409931234","payload":None,"signature":"exact/base64==","eventCommitment":"sha256:original",
               "organizationId":"org-1","timeSource":"system","permitNonce":"nonce-1","edgeVersion":"edge-1.2.3",
               "adapterVersion":None,"externalReceiptRef":"rcpt-9","artifactStorageRef":None,"signatureFormat":2}]
    scope = [{"edgeId":"external","supportStatus":"UNSUPPORTED","maxEvidenceGrade":None,"gapNotes":"Not captured"}]
    coverage = {"organizationId":"org-1","closureCounts":{"OUTCOME_UNKNOWN":12},"openPhaseCounts":{"AUTHORIZED":3},"totalClosed":12,"totalOpen":3}
    detail_route = respx.get(f"{BASE}/protected-actions/action%2Fone").respond(json=detail)
    respx.get(f"{BASE}/protected-actions/action%2Fone/events").respond(json=events)
    respx.get(f"{BASE}/protected-actions/capture-scope").respond(json=scope)
    coverage_route = respx.get(f"{BASE}/protected-actions/coverage-summary").respond(json=coverage)
    sdk = client()
    assert sdk.proof.get("action/one") == detail
    assert b"action%2Fone" in detail_route.calls.last.request.url.raw_path
    assert sdk.proof.events("action/one") == events
    assert sdk.proof.capture_scope() == scope
    sdk.refresh_credential("pk_rotated")
    assert sdk.proof.coverage_summary() == coverage
    assert coverage_route.calls.last.request.headers["Authorization"] == "Bearer pk_rotated"


@respx.mock
def test_default_list_and_access_denial_do_not_become_empty_success():
    route = respx.get(f"{BASE}/protected-actions").respond(403,json={"message":"Denied"})
    with pytest.raises(ForbiddenError):
        client().proof.list()
    assert dict(route.calls.last.request.url.params) == {"page":"1","limit":"100"}
    assert route.call_count == 1


SPEC = Path(os.environ.get("BE_SWAGGER_PATH", Path(__file__).resolve().parents[2] / "be/openapi.json"))


@pytest.mark.skipif(not SPEC.is_file() and os.environ.get("REQUIRE_SWAGGER") != "1", reason="Backend OpenAPI spec is not available")
def test_state_values_match_backend_spec_enum():
    params = json.loads(SPEC.read_text())["paths"]["/organizations/{orgId}/protected-actions"]["get"]["parameters"]
    assert sorted(next(p for p in params if p["name"] == "state")["schema"]["enum"]) == sorted(PROTECTED_ACTION_STATES)


@respx.mock
def test_unknown_state_and_closure_are_sent_unchanged():
    route = respx.get(f"{BASE}/protected-actions").respond(200, json={"data": [], "total": 0, "meta": {}})
    client().proof.list(state="FUTURE_PHASE & review", closure="FUTURE_CLOSURE")
    url = route.calls.last.request.url
    assert b"state=FUTURE_PHASE%20%26%20review" in url.query or b"state=FUTURE_PHASE+%26+review" in url.query
    assert url.params["state"] == "FUTURE_PHASE & review"
    assert url.params["closure"] == "FUTURE_CLOSURE"


@pytest.mark.parametrize("query",[
    {"page":0},{"page":True},{"limit":101},{"limit":1.5},
    {"from_date":"2026-02-30"},{"from_date":"2026-09-01T24:00:00Z"},
    {"from_date":"2026-09-01T10:00:00"},{"from_date":"2026-09-02","to_date":"2026-09-01"},
])
@respx.mock
def test_invalid_query_rejected_without_io(query):
    with pytest.raises(ValueError):
        client().proof.list(**query)
    assert len(respx.calls) == 0


@pytest.mark.parametrize("action_id",["","..","action\nsecret"])
def test_unsafe_action_id_is_rejected(action_id):
    with pytest.raises(ValueError):
        client().proof.get(action_id)


@respx.mock
def test_signed_bundle_returns_bytes_and_uses_from_to_params():
    data = bytes([80,75,3,4,0,255,128])
    route = respx.get(f"{BASE}/audit/bundle").respond(content=data,headers={"content-type":"application/zip"})
    result = client().audit.export_bundle(from_date="2026-01-01T00:00:00+03:00",to_date="2026-04-01T00:00:00+03:00")
    assert result == data
    assert dict(route.calls.last.request.url.params) == {"from":"2026-01-01T00:00:00+03:00","to":"2026-04-01T00:00:00+03:00"}


@pytest.mark.parametrize("start,end",[
    (None,"2026-09-02"),("","2026-09-02"),("2026-09-01","2026-09-01"),
    ("2026-09-02","2026-09-01"),("2026-01-01","2026-04-02"),
    ("2026-01-01T00:00:00Z","2026-04-01T00:00:00.001Z"),("2026-02-30","2026-03-02"),
])
@respx.mock
def test_invalid_bundle_range_fails_before_io(start,end):
    with pytest.raises(ValueError):
        client().audit.export_bundle(from_date=start,to_date=end)
    assert len(respx.calls) == 0


@respx.mock
def test_bundle_denials_and_size_limits_propagate():
    route = respx.get(f"{BASE}/audit/bundle").respond(403,json={"message":"Denied"})
    with pytest.raises(ForbiddenError):
        client().audit.export_bundle(from_date="2026-09-01",to_date="2026-09-02")
    route.respond(content=b"x",headers={"content-length":str(128*1024*1024+1)})
    with pytest.raises(ResponseTooLargeError):
        client().audit.export_bundle(from_date="2026-09-01",to_date="2026-09-02")
