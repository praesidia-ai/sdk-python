"""Tests for praesidia.memory — the agent-memory client (H2-06e)."""

from __future__ import annotations

import json

import httpx
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
    route = respx.post(MEMORIES).mock(
        return_value=httpx.Response(201, json=MEMORY)
    )
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
def test_list_sends_query_params_and_unwraps_envelope():
    route = respx.get(MEMORIES).mock(
        return_value=httpx.Response(200, json={"data": [MEMORY], "total": 1})
    )
    rows = _client().memory.list(limit=5, memory_key="conv-1", tag="crm")
    assert rows == [MEMORY]
    req = route.calls.last.request
    assert req.url.params["limit"] == "5"
    assert req.url.params["memoryKey"] == "conv-1"
    assert req.url.params["tag"] == "crm"


@respx.mock
def test_search_posts_search_memory_dto():
    route = respx.post(f"{MEMORIES}/search").mock(
        return_value=httpx.Response(200, json=[MEMORY])
    )
    hits = _client().memory.search("contact preference", top_k=5)
    assert hits == [MEMORY]
    assert json.loads(route.calls.last.request.content) == {
        "query": "contact preference",
        "topK": 5,
    }


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
    respx.get(f"{MEMORIES}/mem-1").mock(
        return_value=httpx.Response(200, json=MEMORY)
    )
    delete_route = respx.delete(f"{MEMORIES}/mem-1").mock(
        return_value=httpx.Response(204)
    )
    client = _client()
    assert client.memory.get("mem-1")["id"] == "mem-1"
    assert client.memory.delete("mem-1") is None
    assert delete_route.called
