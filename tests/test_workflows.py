"""Backend-contract tests for every Python workflow SDK method."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from praesidia import Praesidia

BASE_URL = "http://test.local"
ORG_ID = "org-1"
BASE = f"{BASE_URL}/organizations/{ORG_ID}/workflows"


def _client() -> Praesidia:
    return Praesidia(api_key="sk-test", org_id=ORG_ID, base_url=BASE_URL)


@respx.mock
def test_workflow_crud_and_pagination_contracts():
    respx.get(BASE).mock(return_value=httpx.Response(200, json={"data": [{"id": "w"}]}))
    assert _client().workflows.list(page=2, limit=10) == [{"id": "w"}]
    assert dict(respx.calls.last.request.url.params) == {"page": "2", "limit": "10"}

    encoded = f"{BASE}/workflow%2Fid"
    respx.get(encoded).mock(return_value=httpx.Response(200, json={"id": "w"}))
    assert _client().workflows.get("workflow/id")["id"] == "w"

    create = respx.post(BASE).mock(return_value=httpx.Response(201, json={"id": "w"}))
    _client().workflows.create({"name": "Review"})
    assert json.loads(create.calls.last.request.content) == {"name": "Review"}

    update = respx.patch(f"{BASE}/w").mock(return_value=httpx.Response(200, json={}))
    _client().workflows.update("w", {"name": "Updated"})
    assert json.loads(update.calls.last.request.content) == {"name": "Updated"}

    delete = respx.delete(f"{BASE}/w").mock(return_value=httpx.Response(204))
    _client().workflows.delete("w")
    assert delete.called


@respx.mock
def test_trigger_wraps_input_in_start_workflow_run_dto():
    route = respx.post(f"{BASE}/w/runs").mock(
        return_value=httpx.Response(201, json={"id": "r"})
    )

    _client().workflows.trigger("w", {"message": "go"}, budget_limit_usd=1.5)

    assert json.loads(route.calls.last.request.content) == {
        "initialInput": {"message": "go"},
        "budgetLimitUsd": 1.5,
    }


@pytest.mark.parametrize("budget", [-1, True, "1"])
def test_trigger_rejects_invalid_budget(budget):
    with pytest.raises(ValueError, match="budget_limit_usd"):
        _client().workflows.trigger("w", budget_limit_usd=budget)


@respx.mock
def test_workflow_run_list_and_get_contracts():
    list_route = respx.get(f"{BASE}/w/runs").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "r"}]})
    )
    assert _client().workflows.list_runs("w", page=3, limit=5) == [{"id": "r"}]
    assert dict(list_route.calls.last.request.url.params) == {
        "page": "3",
        "limit": "5",
    }

    respx.get(f"{BASE}/w/runs/r").mock(
        return_value=httpx.Response(200, json={"id": "r"})
    )
    assert _client().workflows.get_run("w", "r")["id"] == "r"
