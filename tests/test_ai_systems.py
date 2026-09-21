"""Backend-contract tests for every Python AI Systems SDK method (SDK-0002, SDK-0004)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from praesidia import Praesidia

BASE_URL = "http://test.local"
ORG_ID = "org-1"
ORG_BASE = f"{BASE_URL}/organizations/{ORG_ID}"
SYSTEMS = f"{ORG_BASE}/ai-systems"
ASSETS = f"{ORG_BASE}/ai-assets"
RELATIONSHIPS = f"{ORG_BASE}/asset-relationships"


def _client() -> Praesidia:
    return Praesidia(api_key="sk-test", org_id=ORG_ID, base_url=BASE_URL)


@respx.mock
def test_ai_system_crud_and_pagination_contracts():
    respx.get(SYSTEMS).mock(return_value=httpx.Response(200, json={"data": [{"id": "s"}]}))
    assert _client().ai_systems.list(page=2, limit=10) == [{"id": "s"}]
    assert dict(respx.calls.last.request.url.params) == {"page": "2", "limit": "10"}

    respx.get(f"{SYSTEMS}/s").mock(return_value=httpx.Response(200, json={"id": "s"}))
    assert _client().ai_systems.get("s")["id"] == "s"

    create = respx.post(SYSTEMS).mock(return_value=httpx.Response(201, json={"id": "s"}))
    _client().ai_systems.create({"name": "Support triage bot"})
    assert json.loads(create.calls.last.request.content) == {"name": "Support triage bot"}

    update = respx.patch(f"{SYSTEMS}/s").mock(return_value=httpx.Response(200, json={}))
    _client().ai_systems.update("s", {"name": "Updated"})
    assert json.loads(update.calls.last.request.content) == {"name": "Updated"}

    archive = respx.post(f"{SYSTEMS}/s/archive").mock(return_value=httpx.Response(200, json={"id": "s"}))
    _client().ai_systems.archive("s")
    assert archive.called

    restore = respx.post(f"{SYSTEMS}/s/restore").mock(return_value=httpx.Response(200, json={"id": "s"}))
    _client().ai_systems.restore("s")
    assert restore.called


@respx.mock
def test_ai_system_owners_lifecycle_and_delete_contracts():
    owners = respx.patch(f"{SYSTEMS}/s/owners").mock(return_value=httpx.Response(200, json={"id": "s"}))
    _client().ai_systems.update_owners("s", {"ownerType": "user", "ownerId": "u1"})
    assert json.loads(owners.calls.last.request.content) == {"ownerType": "user", "ownerId": "u1"}

    lifecycle = respx.patch(f"{SYSTEMS}/s/lifecycle").mock(return_value=httpx.Response(200, json={"id": "s"}))
    _client().ai_systems.transition_lifecycle("s", "production")
    assert json.loads(lifecycle.calls.last.request.content) == {"lifecycleStatus": "production"}

    with pytest.raises(ValueError):
        _client().ai_systems.transition_lifecycle("s", "not-a-status")

    delete = respx.delete(f"{SYSTEMS}/s").mock(return_value=httpx.Response(204))
    _client().ai_systems.delete("s")
    assert delete.called


@respx.mock
def test_ai_system_list_filters_and_boolean_query_encoding():
    route = respx.get(SYSTEMS).mock(return_value=httpx.Response(200, json={"data": []}))
    _client().ai_systems.list(
        lifecycle_status="production",
        environment="staging",
        criticality="high",
        business_unit="platform",
        owner_id="owner-1",
        include_archived=True,
        q="triage",
    )
    assert dict(route.calls.last.request.url.params) == {
        "page": "1",
        "limit": "20",
        "lifecycleStatus": "production",
        "environment": "staging",
        "criticality": "high",
        "businessUnit": "platform",
        "ownerId": "owner-1",
        "includeArchived": "true",
        "q": "triage",
    }


@respx.mock
def test_ai_system_list_handles_bare_and_legacy_envelopes():
    route = respx.get(SYSTEMS).mock(
        side_effect=[
            httpx.Response(200, json=[{"id": "bare"}]),
            httpx.Response(200, json={"aiSystems": [{"id": "legacy"}]}),
        ]
    )
    client = _client()
    assert client.ai_systems.list() == [{"id": "bare"}]
    assert client.ai_systems.list() == [{"id": "legacy"}]
    assert route.call_count == 2


@respx.mock
def test_ai_system_list_all_auto_paginates():
    respx.get(SYSTEMS).mock(
        side_effect=[
            httpx.Response(200, json={"data": [{"id": "1"}]}),
            httpx.Response(200, json={"data": [{"id": "2"}]}),
            httpx.Response(200, json={"data": []}),
        ]
    )
    assert [row["id"] for row in _client().ai_systems.list_all()] == ["1", "2"]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"lifecycle_status": "not-a-status"},
        {"environment": "not-an-env"},
        {"criticality": "not-a-level"},
    ],
)
def test_ai_system_list_rejects_invalid_enum(kwargs):
    with pytest.raises(ValueError):
        _client().ai_systems.list_page(**kwargs)


@respx.mock
def test_ai_asset_crud_contracts():
    create = respx.post(ASSETS).mock(return_value=httpx.Response(201, json={"id": "a"}))
    _client().ai_systems.create_asset({"name": "Internal audit vendor", "assetType": "VENDOR"})
    assert json.loads(create.calls.last.request.content) == {
        "name": "Internal audit vendor",
        "assetType": "VENDOR",
    }

    respx.get(f"{ASSETS}/a").mock(return_value=httpx.Response(200, json={"id": "a"}))
    assert _client().ai_systems.get_asset("a")["id"] == "a"

    update = respx.patch(f"{ASSETS}/a").mock(return_value=httpx.Response(200, json={"id": "a"}))
    _client().ai_systems.update_asset("a", {"name": "Updated"})
    assert json.loads(update.calls.last.request.content) == {"name": "Updated"}

    archive = respx.post(f"{ASSETS}/a/archive").mock(return_value=httpx.Response(200, json={"id": "a"}))
    _client().ai_systems.archive_asset("a")
    assert archive.called

    restore = respx.post(f"{ASSETS}/a/restore").mock(return_value=httpx.Response(200, json={"id": "a"}))
    _client().ai_systems.restore_asset("a")
    assert restore.called


@respx.mock
def test_ai_asset_list_and_adopt_contracts():
    respx.get(ASSETS).mock(return_value=httpx.Response(200, json={"data": [{"id": "a"}]}))
    assert _client().ai_systems.list_assets(page=1, limit=5) == [{"id": "a"}]
    assert dict(respx.calls.last.request.url.params) == {"page": "1", "limit": "5"}

    adopt = respx.post(f"{ASSETS}/adopt").mock(return_value=httpx.Response(201, json={"id": "a"}))
    _client().ai_systems.adopt_asset({"entityType": "agent", "entityId": "agent-1", "aiSystemId": "s"})
    assert json.loads(adopt.calls.last.request.content) == {
        "entityType": "agent",
        "entityId": "agent-1",
        "aiSystemId": "s",
    }


@respx.mock
def test_ai_asset_list_filters_and_handles_legacy_envelope():
    route = respx.get(ASSETS).mock(
        side_effect=[
            httpx.Response(200, json={"data": []}),
            httpx.Response(200, json={"aiAssets": [{"id": "legacy"}]}),
        ]
    )
    client = _client()
    client.ai_systems.list_assets(
        asset_type="AGENT",
        source="manual",
        discovery_status="adopted",
        environment="production",
        include_archived=False,
    )
    assert dict(route.calls.last.request.url.params) == {
        "page": "1",
        "limit": "20",
        "assetType": "AGENT",
        "source": "manual",
        "discoveryStatus": "adopted",
        "environment": "production",
        "includeArchived": "false",
    }
    assert client.ai_systems.list_assets() == [{"id": "legacy"}]
    assert route.call_count == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"asset_type": "NOT_A_TYPE"},
        {"source": "not-a-source"},
        {"discovery_status": "not-a-status"},
    ],
)
def test_ai_asset_list_rejects_invalid_enum(kwargs):
    with pytest.raises(ValueError):
        _client().ai_systems.list_assets_page(**kwargs)


@respx.mock
def test_ai_system_asset_membership_contracts():
    attach = respx.post(f"{SYSTEMS}/s/assets").mock(
        return_value=httpx.Response(201, json={"id": "m", "role": "primary"})
    )
    _client().ai_systems.attach_asset("s", {"assetId": "a", "role": "primary"})
    assert json.loads(attach.calls.last.request.content) == {"assetId": "a", "role": "primary"}

    role = respx.patch(f"{SYSTEMS}/s/assets/a/role").mock(
        return_value=httpx.Response(200, json={"id": "m", "role": "supporting"})
    )
    _client().ai_systems.change_asset_role("s", "a", "supporting")
    assert json.loads(role.calls.last.request.content) == {"role": "supporting"}

    detach = respx.delete(f"{SYSTEMS}/s/assets/a").mock(return_value=httpx.Response(204))
    _client().ai_systems.detach_asset("s", "a")
    assert detach.called


@respx.mock
def test_asset_relationship_create_and_list_contracts():
    create = respx.post(RELATIONSHIPS).mock(
        return_value=httpx.Response(201, json={"id": "r", "relationshipType": "CALLS"})
    )
    _client().ai_systems.create_relationship(
        {"sourceAssetId": "a1", "targetAssetId": "a2", "relationshipType": "CALLS"}
    )
    assert json.loads(create.calls.last.request.content) == {
        "sourceAssetId": "a1",
        "targetAssetId": "a2",
        "relationshipType": "CALLS",
    }

    route = respx.get(RELATIONSHIPS).mock(return_value=httpx.Response(200, json={"data": [{"id": "r"}]}))
    assert _client().ai_systems.list_relationships(
        source_asset_id="a1",
        target_asset_id="a2",
        asset_id="a1",
        relationship_type="CALLS",
        include_archived=True,
    ) == [{"id": "r"}]
    assert dict(route.calls.last.request.url.params) == {
        "page": "1",
        "limit": "20",
        "sourceAssetId": "a1",
        "targetAssetId": "a2",
        "assetId": "a1",
        "relationshipType": "CALLS",
        "includeArchived": "true",
    }


@respx.mock
def test_asset_relationship_get_update_archive_restore_contracts():
    respx.get(f"{RELATIONSHIPS}/r").mock(return_value=httpx.Response(200, json={"id": "r"}))
    assert _client().ai_systems.get_relationship("r")["id"] == "r"

    update = respx.patch(f"{RELATIONSHIPS}/r").mock(
        return_value=httpx.Response(200, json={"id": "r", "version": 2})
    )
    _client().ai_systems.update_relationship("r", {"confidence": 0.5})
    assert json.loads(update.calls.last.request.content) == {"confidence": 0.5}

    archive = respx.post(f"{RELATIONSHIPS}/r/archive").mock(
        return_value=httpx.Response(200, json={"id": "r"})
    )
    _client().ai_systems.archive_relationship("r")
    assert archive.called

    restore = respx.post(f"{RELATIONSHIPS}/r/restore").mock(
        return_value=httpx.Response(200, json={"id": "r"})
    )
    _client().ai_systems.restore_relationship("r")
    assert restore.called


@respx.mock
def test_asset_relationship_list_handles_bare_and_legacy_envelopes():
    route = respx.get(RELATIONSHIPS).mock(
        side_effect=[
            httpx.Response(200, json=[{"id": "bare"}]),
            httpx.Response(200, json={"relationships": [{"id": "legacy"}]}),
        ]
    )
    client = _client()
    assert client.ai_systems.list_relationships() == [{"id": "bare"}]
    assert client.ai_systems.list_relationships() == [{"id": "legacy"}]
    assert route.call_count == 2


@respx.mock
def test_asset_relationship_list_all_auto_paginates():
    respx.get(RELATIONSHIPS).mock(
        side_effect=[
            httpx.Response(200, json={"data": [{"id": "1"}]}),
            httpx.Response(200, json={"data": []}),
        ]
    )
    assert [row["id"] for row in _client().ai_systems.list_relationships_all()] == ["1"]


def test_asset_relationship_list_rejects_invalid_enum():
    with pytest.raises(ValueError):
        _client().ai_systems.list_relationships_page(relationship_type="NOT_A_TYPE")


@respx.mock
def test_traverse_contracts_and_query_encoding():
    route = respx.get(f"{RELATIONSHIPS}/graph/traverse").mock(
        return_value=httpx.Response(
            200, json={"nodes": [], "edges": [], "stats": {"depth": 3, "nodeCount": 0, "edgeCount": 0, "depthClamped": False, "truncated": False}}
        )
    )
    result = _client().ai_systems.traverse(
        "a1",
        direction="upstream",
        max_depth=5,
        asset_types=["AGENT", "MODEL"],
        relationship_types=["CALLS"],
        include_archived=True,
    )
    assert result["stats"]["nodeCount"] == 0
    params = route.calls.last.request.url.params
    assert params["assetId"] == "a1"
    assert params["direction"] == "upstream"
    assert params["maxDepth"] == "5"
    assert params.get_list("assetTypes") == ["AGENT", "MODEL"]
    assert params.get_list("relationshipTypes") == ["CALLS"]
    assert params["includeArchived"] == "true"


@respx.mock
def test_traverse_defaults_omit_optional_params():
    route = respx.get(f"{RELATIONSHIPS}/graph/traverse").mock(
        return_value=httpx.Response(
            200, json={"nodes": [], "edges": [], "stats": {"depth": 3, "nodeCount": 0, "edgeCount": 0, "depthClamped": False, "truncated": False}}
        )
    )
    _client().ai_systems.traverse("a1")
    assert dict(route.calls.last.request.url.params) == {"assetId": "a1", "direction": "downstream"}


def test_traverse_rejects_invalid_direction():
    with pytest.raises(ValueError):
        _client().ai_systems.traverse("a1", direction="not-a-direction")


@respx.mock
def test_ai_system_summary_contract():
    respx.get(f"{SYSTEMS}/s/summary").mock(
        return_value=httpx.Response(
            200,
            json={
                "compliance": {"available": True, "counts": {"low_risk": 1}},
                "risk": {"available": True, "counts": {}},
                "evaluations": {"available": True, "counts": {}},
                "cost": {"available": False, "reason": "AISYS-0025"},
                "evidence": {"available": True, "counts": {}},
                "unlinkedAssets": 2,
            },
        )
    )
    result = _client().ai_systems.summary("s")
    assert result["unlinkedAssets"] == 2
    assert result["cost"] == {"available": False, "reason": "AISYS-0025"}
