import httpx
import pytest
import respx

from praesidia import Praesidia, ForbiddenError, ResponseTooLargeError

BASE = "https://api.test/organizations/org-1"


def client():
    return Praesidia(api_key="pk_personal", org_id="org-1", base_url="https://api.test", retry=False)


@respx.mock
def test_list_preserves_envelope_and_exact_backend_query():
    response = {"data": [{"actionId": "action1", "closure": "SUCCEEDED", "evidenceGrade": "C"}], "total": 102, "meta": {"page": 2, "limit": 10, "total": 102, "totalPages": 11, "hasNextPage": True, "hasPrevPage": True}}
    route = respx.get(f"{BASE}/protected-actions").respond(json=response)
    assert client().proof.list(agent_id="agent-id",task_id="task-id",chain_id="chain-id",state="OUTCOME_UNKNOWN & review",closure="OUTCOME_UNKNOWN",from_date="2026-09-01T00:00:00+03:00",to_date="2026-09-02T00:00:00+03:00",page=2,limit=10) == response
    request = route.calls.last.request
    assert dict(request.url.params) == {"agentId":"agent-id","taskId":"task-id","chainId":"chain-id","state":"OUTCOME_UNKNOWN & review","closure":"OUTCOME_UNKNOWN","from":"2026-09-01T00:00:00+03:00","to":"2026-09-02T00:00:00+03:00","page":"2","limit":"10"}
    assert request.headers["Authorization"] == "Bearer pk_personal"


@respx.mock
def test_detail_events_scope_and_coverage_preserve_evidence_without_verifying():
    detail = {"actionId":"action1","closure":"SUCCEEDED","verificationStatus":"INCOMPLETE","evidenceGrade":None}
    events = [{"actionSeq":"90071992547409931234","payload":None,"signature":"exact/base64==","eventCommitment":"sha256:original"}]
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


@pytest.mark.parametrize("query",[
    {"page":0},{"page":True},{"limit":101},{"limit":1.5},{"closure":"SUCCESS"},
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
