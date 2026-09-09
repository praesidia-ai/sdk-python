import json

import httpx
import pytest
import respx

from praesidia.identity import ACCESS_TOKEN_TYPE, IdentityClient, IdentityError

TOKEN = "pfa_00000000-0000-4000-8000-000000000001." + "a" * 43
RESULT = {"access_token": TOKEN, "issued_token_type": ACCESS_TOKEN_TYPE, "token_type": "Bearer", "expires_in": 299, "scope": "agents:invoke mcp:invoke"}
INPUT = {"organization_id": "org", "subject_binding_id": "workload", "subject_token": "fresh.jwt.assertion", "resource": "https://api.test", "scopes": ["agents:invoke", "mcp:invoke"]}


@respx.mock
def test_exact_delegation_contract_has_no_management_authorization():
    route = respx.post("https://api.test/oauth/token-exchange").respond(json=RESULT)
    with IdentityClient("https://api.test") as client:
        assert client.exchange(**INPUT, actor_binding_id="actor", actor_token="actor.jwt.assertion", consent_id="consent") == RESULT
    request = route.calls.last.request
    assert "authorization" not in request.headers
    body = json.loads(request.content)
    assert body["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
    assert body["actor_token_type"] == "urn:ietf:params:oauth:token-type:jwt"
    assert body["actor_binding_id"] == "actor"
    assert body["consent_id"] == "consent"


@respx.mock
def test_downexchange_binds_parent_resource_and_omits_scope_by_default():
    route = respx.post("https://api.test/oauth/token-exchange").respond(json=RESULT)
    with IdentityClient("https://api.test") as client:
        client.down_exchange(TOKEN, subject_resource="https://mcp.test/mcp", resource="https://api.test")
    body = json.loads(route.calls.last.request.content)
    assert body["subject_resource"] == "https://mcp.test/mcp"
    assert body["resource"] == "https://api.test"
    assert "scope" not in body


@respx.mock
def test_redirects_and_assertion_errors_are_not_replayed_or_followed():
    route = respx.post("https://api.test/oauth/token-exchange").respond(307, headers={"Location": "https://evil.test/collect"})
    with IdentityClient("https://api.test") as client:
        with pytest.raises(IdentityError, match="307"):
            client.exchange(**INPUT)
    assert route.call_count == 1
    assert len(respx.calls) == 1


@respx.mock
def test_errors_never_echo_assertions_and_network_failures_do_not_retry():
    route = respx.post("https://api.test/oauth/token-exchange").respond(401, json={"error": INPUT["subject_token"]})
    with IdentityClient("https://api.test") as client:
        with pytest.raises(IdentityError) as caught:
            client.exchange(**INPUT)
        assert INPUT["subject_token"] not in str(caught.value)
    assert route.call_count == 1


@respx.mock
def test_use_time_provider_gets_a_new_assertion_and_introspection_is_live():
    route = respx.post("https://api.test/oauth/token-exchange").respond(json=RESULT)
    introspection = respx.post("https://api.test/identity/introspect").respond(json={"grantId": "grant"})
    requests = iter([INPUT, {**INPUT, "subject_token": "next.jwt.assertion"}])
    with IdentityClient("https://api.test") as client:
        acquire = client.credential_provider(lambda: next(requests))
        assert acquire() == TOKEN
        assert acquire() == TOKEN
        assert json.loads(route.calls.last.request.content)["subject_token"] == "next.jwt.assertion"
        client.introspect(TOKEN)
        client.introspect(TOKEN)
    assert introspection.call_count == 2
    assert introspection.calls.last.request.headers["authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize("base", ["http://remote.test", "https://user:secret@api.test", "https://api.test/#x"])
def test_invalid_exchange_endpoints_rejected_without_network(base):
    with pytest.raises(ValueError):
        IdentityClient(base)


@respx.mock
def test_wildcards_partial_delegation_and_oversized_responses_fail_closed():
    with IdentityClient("https://api.test") as client:
        with pytest.raises(ValueError):
            client.exchange(**{**INPUT, "scopes": ["*"]})
        with pytest.raises(ValueError):
            client.exchange(**INPUT, actor_binding_id="actor")
        assert len(respx.calls) == 0
        respx.post("https://api.test/oauth/token-exchange").respond(content=b" " * 65537)
        with pytest.raises(IdentityError, match="exceeds limit"):
            client.exchange(**INPUT)
