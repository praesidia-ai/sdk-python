"""SCAN2-011 -- list()/list_runs() unwrap be's paginated envelope into a bare
list typed as the whole collection, discarding ``meta`` entirely. A caller
iterating the returned list has no way to detect that more rows exist. These
tests drive the real HTTP boundary (respx stubbing the actual wire response)
and assert (a) the new ``list_page()``/``list_all()`` give the caller a way
to detect/consume truncation and (b) the original ``list()``/``list_runs()``
keep their exact prior list-returning signature (backwards compatible).
"""

from __future__ import annotations

import math

import httpx
import respx

from praesidia import Praesidia

BASE_URL = "http://test.local"
ORG_ID = "org-1"
AGENTS_URL = f"{BASE_URL}/organizations/{ORG_ID}/agents"
CONNECTIONS_URL = f"{BASE_URL}/organizations/{ORG_ID}/connections"
WORKFLOWS_URL = f"{BASE_URL}/organizations/{ORG_ID}/workflows"
WORKFLOW_ID = "wf-1"
RUNS_URL = f"{WORKFLOWS_URL}/{WORKFLOW_ID}/runs"


def _client() -> Praesidia:
    return Praesidia(api_key="sk-test", org_id=ORG_ID, base_url=BASE_URL)


def _envelope(page: int, ids: list[str], total: int, limit: int) -> dict:
    return {
        "data": [{"id": i} for i in ids],
        "total": total,
        "meta": {
            "page": page,
            "limit": limit,
            "total": total,
            "totalPages": math.ceil(total / limit),
            "hasNextPage": page * limit < total,
            "hasPrevPage": page > 1,
        },
    }


@respx.mock
def test_agents_list_stays_a_bare_list_with_no_truncation_signal():
    respx.get(AGENTS_URL, params={"page": 1, "limit": 20}).mock(
        return_value=httpx.Response(200, json=_envelope(1, ["a1", "a2"], 100, 2))
    )
    client = _client()
    result = client.agents.list()
    assert isinstance(result, list)
    assert result == [{"id": "a1"}, {"id": "a2"}]


@respx.mock
def test_agents_list_page_exposes_total_and_has_next_page():
    respx.get(AGENTS_URL, params={"page": 1, "limit": 20}).mock(
        return_value=httpx.Response(200, json=_envelope(1, ["a1", "a2"], 100, 2))
    )
    client = _client()
    page = client.agents.list_page()
    assert page["data"] == [{"id": "a1"}, {"id": "a2"}]
    assert page["total"] == 100
    assert page["meta"]["hasNextPage"] is True


@respx.mock
def test_agents_list_all_auto_paginates_across_every_page():
    respx.get(AGENTS_URL, params={"page": 1, "limit": 2}).mock(
        return_value=httpx.Response(200, json=_envelope(1, ["a1", "a2"], 5, 2))
    )
    respx.get(AGENTS_URL, params={"page": 2, "limit": 2}).mock(
        return_value=httpx.Response(200, json=_envelope(2, ["a3", "a4"], 5, 2))
    )
    respx.get(AGENTS_URL, params={"page": 3, "limit": 2}).mock(
        return_value=httpx.Response(200, json=_envelope(3, ["a5"], 5, 2))
    )
    respx.get(AGENTS_URL, params={"page": 4, "limit": 2}).mock(
        return_value=httpx.Response(200, json=_envelope(4, [], 5, 2))
    )
    client = _client()
    all_agents = list(client.agents.list_all(limit=2))
    assert all_agents == [{"id": "a1"}, {"id": "a2"}, {"id": "a3"}, {"id": "a4"}, {"id": "a5"}]


@respx.mock
def test_connections_list_page_and_list_all_give_the_same_guarantees():
    respx.get(CONNECTIONS_URL, params={"page": 1, "limit": 1}).mock(
        return_value=httpx.Response(200, json=_envelope(1, ["c1"], 2, 1))
    )
    respx.get(CONNECTIONS_URL, params={"page": 2, "limit": 1}).mock(
        return_value=httpx.Response(200, json=_envelope(2, ["c2"], 2, 1))
    )
    respx.get(CONNECTIONS_URL, params={"page": 3, "limit": 1}).mock(
        return_value=httpx.Response(200, json=_envelope(3, [], 2, 1))
    )
    client = _client()
    page = client.connections.list_page(limit=1)
    assert page["total"] == 2
    assert page["meta"]["hasNextPage"] is True
    all_conns = list(client.connections.list_all(limit=1))
    assert all_conns == [{"id": "c1"}, {"id": "c2"}]


@respx.mock
def test_workflows_list_page_list_all_and_list_runs_page_list_runs_all():
    respx.get(WORKFLOWS_URL, params={"page": 1, "limit": 1}).mock(
        return_value=httpx.Response(200, json=_envelope(1, ["w1"], 2, 1))
    )
    respx.get(WORKFLOWS_URL, params={"page": 2, "limit": 1}).mock(
        return_value=httpx.Response(200, json=_envelope(2, ["w2"], 2, 1))
    )
    respx.get(WORKFLOWS_URL, params={"page": 3, "limit": 1}).mock(
        return_value=httpx.Response(200, json=_envelope(3, [], 2, 1))
    )
    client = _client()
    page = client.workflows.list_page(limit=1)
    assert page["total"] == 2
    all_workflows = list(client.workflows.list_all(limit=1))
    assert all_workflows == [{"id": "w1"}, {"id": "w2"}]

    respx.get(RUNS_URL, params={"page": 1, "limit": 1}).mock(
        return_value=httpx.Response(200, json=_envelope(1, ["r1"], 2, 1))
    )
    respx.get(RUNS_URL, params={"page": 2, "limit": 1}).mock(
        return_value=httpx.Response(200, json=_envelope(2, ["r2"], 2, 1))
    )
    respx.get(RUNS_URL, params={"page": 3, "limit": 1}).mock(
        return_value=httpx.Response(200, json=_envelope(3, [], 2, 1))
    )
    runs_page = client.workflows.list_runs_page(WORKFLOW_ID, limit=1)
    assert runs_page["total"] == 2
    all_runs = list(client.workflows.list_runs_all(WORKFLOW_ID, limit=1))
    assert all_runs == [{"id": "r1"}, {"id": "r2"}]
