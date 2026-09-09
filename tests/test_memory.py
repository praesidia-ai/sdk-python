"""Tests for praesidia.memory — the agent-memory client (H2-06e)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from praesidia import Praesidia

BASE_URL = "http://test.local"
ORG_ID = "org-1"
MEMORIES = f"{BASE_URL}/organizations/{ORG_ID}/memories"

MEMORY = {
    "id": "mem-1",
    "organizationId": ORG_ID,
    "content": "The customer prefers email.",
    "memoryKey": None,
    "tags": None,
    "provenance": {
        "sourceType": "agent",
        "sourceAgentId": None,
        "authorUserId": "u-1",
        "sourceReference": None,
        "writtenAt": "2026-07-06T00:00:00.000Z",
    },
    "guardrail": {"poisoningScore": 0.01, "piiRedacted": False},
    "retention": {"regime": "none", "expiresAt": None},
    "erasedAt": None,
    "createdAt": "2026-07-06T00:00:00.000Z",
    "updatedAt": "2026-07-06T00:00:00.000Z",
}


def _client() -> Praesidia:
    return Praesidia(api_key="sk-test", org_id=ORG_ID, base_url=BASE_URL)


@respx.mock
def test_create_posts_create_memory_dto():
    route = respx.post(MEMORIES).mock(return_value=httpx.Response(201, json=MEMORY))
    result = _client().memory.create(
        content="The customer prefers email.",
        subject_id="subject-9",
        tags=["crm"],
    )
    assert route.called
    assert json.loads(route.calls.last.request.content) == {
        "content": "The customer prefers email.",
        "subjectId": "subject-9",
        "tags": ["crm"],
    }
    assert result["id"] == "mem-1"


@respx.mock
def test_create_uses_exact_backend_memory_enums():
    route = respx.post(MEMORIES).mock(return_value=httpx.Response(201, json=MEMORY))

    _client().memory.create(
        "retained",
        memory_key="namespace-1",
        source_type="IMPORT",
        access_source_id="00000000-0000-4000-8000-000000000002",
        source_agent_id="00000000-0000-4000-8000-000000000001",
        source_reference="import-job-1",
        retention_regime="CUSTOM",
        retention_days=30,
    )

    assert json.loads(route.calls.last.request.content) == {
        "content": "retained",
        "memoryKey": "namespace-1",
        "sourceType": "IMPORT",
        "accessSourceId": "00000000-0000-4000-8000-000000000002",
        "sourceAgentId": "00000000-0000-4000-8000-000000000001",
        "sourceReference": "import-job-1",
        "retentionRegime": "CUSTOM",
        "retentionDays": 30,
    }
    assert "SOC2" in _client().memory.RETENTION_REGIMES


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"source_type": "agent"}, "source_type"),
        ({"source_type": "TOOL"}, "source_type"),
        ({"retention_regime": "sox"}, "retention_regime"),
        ({"retention_regime": "SOX"}, "retention_regime"),
        ({"retention_days": 0}, "retention_days"),
        ({"retention_days": 1.5}, "retention_days"),
        ({"retention_days": True}, "retention_days"),
        ({"retention_days": 30}, "only valid"),
        ({"retention_regime": "SOC2", "retention_days": 30}, "only valid"),
        ({"retention_regime": "CUSTOM"}, "required"),
    ],
)
@respx.mock
def test_create_rejects_invalid_or_silently_ignored_values(kwargs, message):
    route = respx.post(MEMORIES)

    with pytest.raises(ValueError, match=message):
        _client().memory.create("content", **kwargs)

    assert not route.called


@pytest.mark.parametrize("content", ["", 123, "x" * 32_769])
def test_create_rejects_invalid_content_before_network(content):
    with pytest.raises(ValueError, match="content"):
        _client().memory.create(content)


def test_list_rejects_invalid_source_type_before_network():
    with pytest.raises(ValueError, match="source_type"):
        _client().memory.list(source_type="agent")


@respx.mock
def test_list_sends_query_params_and_unwraps_envelope():
    route = respx.get(MEMORIES).mock(
        return_value=httpx.Response(200, json={"data": [MEMORY], "total": 1})
    )
    rows = _client().memory.list(
        limit=5, memory_key="conv-1", source_type="AGENT", tag="crm"
    )
    assert rows == [MEMORY]
    req = route.calls.last.request
    assert req.url.params["limit"] == "5"
    assert req.url.params["memoryKey"] == "conv-1"
    assert req.url.params["sourceType"] == "AGENT"
    assert req.url.params["tag"] == "crm"


@respx.mock
def test_search_posts_search_memory_dto():
    route = respx.post(f"{MEMORIES}/search").mock(
        return_value=httpx.Response(200, json=[MEMORY])
    )
    hits = _client().memory.search("contact preference", memory_key="conv-1", top_k=5)
    assert hits == [MEMORY]
    assert json.loads(route.calls.last.request.content) == {
        "query": "contact preference",
        "memoryKey": "conv-1",
        "topK": 5,
    }


@pytest.mark.parametrize("query", ["", 123, "x" * 4_097])
def test_search_rejects_invalid_query_before_network(query):
    with pytest.raises(ValueError, match="query"):
        _client().memory.search(query)


@pytest.mark.parametrize("top_k", [0, 51, True, 1.5])
def test_search_rejects_invalid_top_k_before_network(top_k):
    with pytest.raises(ValueError, match="top_k"):
        _client().memory.search("query", top_k=top_k)


@respx.mock
def test_erase_posts_subject_and_reason():
    route = respx.post(f"{MEMORIES}/erase").mock(
        return_value=httpx.Response(
            200,
            json={
                "subjectExternalIdHash": "hash-abc",
                "memoriesErased": 3,
                "dekDestroyed": True,
                "certificateId": "cert-1",
            },
        )
    )
    res = _client().memory.erase("subject-9", "GDPR Art-17 request")
    assert res["memoriesErased"] == 3
    assert res["dekDestroyed"] is True
    assert json.loads(route.calls.last.request.content) == {
        "subjectId": "subject-9",
        "reason": "GDPR Art-17 request",
    }


@respx.mock
def test_get_and_delete():
    respx.get(f"{MEMORIES}/mem-1").mock(return_value=httpx.Response(200, json=MEMORY))
    delete_route = respx.delete(f"{MEMORIES}/mem-1").mock(
        return_value=httpx.Response(204)
    )
    client = _client()
    assert client.memory.get("mem-1")["id"] == "mem-1"
    assert client.memory.delete("mem-1") is None
    assert delete_route.called


@respx.mock
def test_source_authority_contract_and_revision_conflict():
    sources = f"{BASE_URL}/organizations/{ORG_ID}/memory-sources"
    route = respx.post(sources + "/synchronize").mock(return_value=httpx.Response(201, json={"id": "source-1", "revision": 5}))
    respx.get(sources).mock(return_value=httpx.Response(200, json=[{"id": "source-1"}]))
    client = _client()
    result = client.memory.synchronize_source(source_reference="doc/1", content_version="v2", authority_url="https://authority.example.test/check", authority_public_key="public-key", allowed_user_ids=["reader-id"], valid_until="2026-09-05T12:10:00Z", expected_revision=4, state="active")
    assert result == {"id": "source-1", "revision": 5}
    assert json.loads(route.calls.last.request.content) == {"sourceReference": "doc/1", "contentVersion": "v2", "authorityUrl": "https://authority.example.test/check", "authorityPublicKey": "public-key", "allowedUserIds": ["reader-id"], "validUntil": "2026-09-05T12:10:00Z", "expectedRevision": 4, "state": "active"}
    assert client.memory.list_sources() == [{"id": "source-1"}]
