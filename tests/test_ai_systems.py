"""Backend-contract tests for every Python AI Systems SDK method (SDK-0002, SDK-0004)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest
import respx

from praesidia import Praesidia
from praesidia.ai_systems import AiSystemsResource
from praesidia.exceptions import PraesidiaConfigError

# SDK-0303 -- spec-path resolution mirrors sdk's scripts/audit-api-contract.mjs:
# BE_SWAGGER_PATH override > ../../ui/swagger.json sibling checkout (this
# monorepo's committed, gate-verified spec; be's frozen openapi.json export is a stale
# snapshot nothing regenerates -- see SDK-0303). Skips with a reason (never
# fails) when neither exists, so this package still tests from a bare
# sdk-python clone with no ui sibling.
_env_override = os.environ.get("BE_SWAGGER_PATH")
if _env_override and Path(_env_override).resolve().is_file():
    _SWAGGER_PATH = Path(_env_override).resolve()
else:
    _SWAGGER_PATH = (Path(__file__).resolve().parents[2] / "ui" / "swagger.json")
_SWAGGER_AVAILABLE = _SWAGGER_PATH.is_file()

BASE_URL = "https://test.local"
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
    _client().ai_systems.transition_lifecycle("s", "development")
    assert json.loads(lifecycle.calls.last.request.content) == {"lifecycleStatus": "development"}

    with pytest.raises(ValueError):
        _client().ai_systems.transition_lifecycle("s", "not-a-status")

    delete = respx.delete(f"{SYSTEMS}/s").mock(return_value=httpx.Response(204))
    _client().ai_systems.delete("s")
    assert delete.called


# SDK-0323 — AISYS-0018 approval-gated lifecycle (be ai-systems.controller.ts).


@respx.mock
@pytest.mark.parametrize("target", ["production", "retired"])
def test_lifecycle_patch_to_gated_target_fails_fast(target):
    # be ai-systems.service.ts:408-416 400s every direct PATCH into a gated state.
    patch = respx.patch(f"{SYSTEMS}/s/lifecycle")
    with pytest.raises(PraesidiaConfigError, match="retire" if target == "retired" else "request_lifecycle_transition"):
        _client().ai_systems.transition_lifecycle("s", target)
    assert not patch.called


@respx.mock
def test_request_lifecycle_transition_contract():
    route = respx.post(f"{SYSTEMS}/s/lifecycle-requests").mock(
        return_value=httpx.Response(201, json={"id": "r1", "status": "PENDING"})
    )
    out = _client().ai_systems.request_lifecycle_transition("s", "production", reason="go live")
    assert out == {"id": "r1", "status": "PENDING"}
    assert json.loads(route.calls.last.request.content) == {"toStatus": "production", "reason": "go live"}

    _client().ai_systems.request_lifecycle_transition("s", "production")
    assert json.loads(route.calls.last.request.content) == {"toStatus": "production"}


@respx.mock
def test_request_lifecycle_transition_rejects_retired_and_unknown_locally():
    route = respx.post(f"{SYSTEMS}/s/lifecycle-requests")
    # be controller :164 400s toStatus 'retired' (BE-0932: retire has one door).
    with pytest.raises(ValueError, match="retire"):
        _client().ai_systems.request_lifecycle_transition("s", "retired")
    with pytest.raises(ValueError):
        _client().ai_systems.request_lifecycle_transition("s", "not-a-status")
    assert not route.called


@respx.mock
@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_decide_lifecycle_transition_contract(decision):
    route = respx.post(f"{SYSTEMS}/lifecycle-requests/r1/{decision}").mock(
        return_value=httpx.Response(201, json={"id": "r1"})
    )
    method = getattr(_client().ai_systems, f"{decision}_lifecycle_transition")
    assert method("r1", reason="ok by owner") == {"id": "r1"}
    assert json.loads(route.calls.last.request.content) == {"reason": "ok by owner"}
    method("r1")
    assert json.loads(route.calls.last.request.content) == {}


@respx.mock
def test_retire_lifecycle_contract():
    route = respx.post(f"{SYSTEMS}/s/retire").mock(
        return_value=httpx.Response(202, json={"requestId": "r2", "preview": {}})
    )
    out = _client().ai_systems.retire(
        "s", retention_policy="Keep evidence 7 years.", reason="Superseded by v3."
    )
    assert out["requestId"] == "r2"
    assert json.loads(route.calls.last.request.content) == {
        "retentionPolicy": "Keep evidence 7 years.",
        "reason": "Superseded by v3.",
    }
    _client().ai_systems.retire(
        "s",
        retention_policy="Keep evidence 7 years.",
        reason="Superseded by v3.",
        retention_until="2033-01-31T00:00:00.000Z",
    )
    assert json.loads(route.calls.last.request.content)["retentionUntil"] == "2033-01-31T00:00:00.000Z"


@respx.mock
def test_reapprove_lifecycle_contract():
    change = "00000000-0000-4000-8000-000000000001"
    route = respx.post(f"{SYSTEMS}/s/reapprove").mock(return_value=httpx.Response(200, json={"id": "s"}))
    assert _client().ai_systems.reapprove("s", change, reason="reviewed") == {"id": "s"}
    assert json.loads(route.calls.last.request.content) == {"materialChangeId": change, "reason": "reviewed"}
    _client().ai_systems.reapprove("s", change)
    assert json.loads(route.calls.last.request.content) == {"materialChangeId": change}


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


def test_traverse_rejects_invalid_asset_type():
    with pytest.raises(ValueError):
        _client().ai_systems.traverse("a1", asset_types=["NOT_A_TYPE"])


def test_traverse_rejects_invalid_relationship_type():
    with pytest.raises(ValueError):
        _client().ai_systems.traverse("a1", relationship_types=["NOT_A_RELATIONSHIP"])


@pytest.mark.skipif(
    not _SWAGGER_AVAILABLE,
    reason=(
        f"no swagger.json at {_SWAGGER_PATH} (BE_SWAGGER_PATH override or "
        "ui/swagger.json sibling checkout) -- see SDK-0303"
    ),
)
def test_asset_and_relationship_types_match_openapi():
    """SDK-0007 — fails if be's enum widens/shrinks again without an SDK sync.

    Reads the gate-verified `ui/swagger.json` (never regenerated here) and
    compares its `AiAsset.assetType` / `AssetRelationship.relationshipType`
    enums against `AiSystemsResource.ASSET_TYPES` / `RELATIONSHIP_TYPES`.
    """
    spec = json.loads(_SWAGGER_PATH.read_text())
    schemas = spec["components"]["schemas"]
    openapi_asset_types = set(schemas["AiAsset"]["properties"]["assetType"]["enum"])
    openapi_relationship_types = set(
        schemas["AssetRelationship"]["properties"]["relationshipType"]["enum"]
    )
    assert set(AiSystemsResource.ASSET_TYPES) == openapi_asset_types
    assert set(AiSystemsResource.RELATIONSHIP_TYPES) == openapi_relationship_types


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


@respx.mock
def test_put_system_by_external_id_idempotent_on_repeat():
    """SDK-0302/PRAE-228/229 — same body twice returns changed: False the
    second time, with an unchanged updatedAt (be's BE-0579 semantics)."""
    body = {"name": "Support triage bot"}
    first = {
        "id": "s",
        "externalId": "ext-1",
        "created": True,
        "changed": True,
        "updatedAt": "2026-09-22T00:00:00Z",
        "resource": {"id": "s", "externalId": "ext-1", "name": "Support triage bot"},
    }
    second = {**first, "created": False, "changed": False}
    route = respx.put(f"{SYSTEMS}/by-external-id/ext-1").mock(
        side_effect=[httpx.Response(200, json=first), httpx.Response(200, json=second)]
    )
    client = _client()
    first_result = client.ai_systems.put_system_by_external_id("ext-1", body)
    second_result = client.ai_systems.put_system_by_external_id("ext-1", body)
    assert json.loads(route.calls.last.request.content) == body
    assert first_result["created"] is True and first_result["changed"] is True
    assert second_result["changed"] is False
    assert second_result["updatedAt"] == first_result["updatedAt"]


@respx.mock
def test_delete_system_by_external_id_returns_outcome_body():
    route = respx.delete(f"{SYSTEMS}/by-external-id/ext-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "s",
                "externalId": "ext-1",
                "created": False,
                "changed": True,
                "updatedAt": "2026-09-22T00:00:01Z",
                "resource": {"id": "s", "externalId": "ext-1", "archivedAt": "2026-09-22T00:00:01Z"},
            },
        )
    )
    result = _client().ai_systems.delete_system_by_external_id("ext-1")
    assert result["resource"]["archivedAt"] == "2026-09-22T00:00:01Z"
    assert route.called


@respx.mock
def test_put_asset_by_external_id_contract_and_enum_validation():
    route = respx.put(f"{ASSETS}/by-external-id/ext-asset-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "a",
                "externalId": "ext-asset-1",
                "created": True,
                "changed": True,
                "updatedAt": "2026-09-22T00:00:00Z",
                "resource": {"id": "a", "externalId": "ext-asset-1", "assetType": "VENDOR"},
            },
        )
    )
    result = _client().ai_systems.put_asset_by_external_id(
        "ext-asset-1", {"name": "Vendor Co", "assetType": "VENDOR"}
    )
    assert result["created"] is True
    assert route.called
    with pytest.raises(ValueError):
        _client().ai_systems.put_asset_by_external_id(
            "ext-asset-1", {"name": "x", "assetType": "NOT_A_TYPE"}
        )


@respx.mock
def test_asset_writes_reject_pipeline_sources_but_list_filter_keeps_them():
    """SDK-0318 / BE-1529 -- create/put send only `manual`/`api`/`import`;
    the list filter still accepts every `AI_ASSET_SOURCES` value."""
    post = respx.post(ASSETS).mock(return_value=httpx.Response(201, json={"id": "a"}))
    put = respx.put(f"{ASSETS}/by-external-id/e").mock(return_value=httpx.Response(200, json={}))
    listed = respx.get(ASSETS).mock(
        return_value=httpx.Response(200, json={"data": [], "total": 0, "page": 1, "limit": 20})
    )
    ai = _client().ai_systems
    for source in ("discovery_connector", "runtime_observation", "entitlement_projection"):
        with pytest.raises(ValueError, match="source must be one of"):
            ai.create_asset({"name": "x", "assetType": "VENDOR", "source": source})
        with pytest.raises(ValueError, match="source must be one of"):
            ai.put_asset_by_external_id("e", {"name": "x", "assetType": "VENDOR", "source": source})
        ai.list_assets_page(source=source)
        assert listed.calls.last.request.url.params["source"] == source
    assert not post.called and not put.called
    for source in AiSystemsResource.CLIENT_ASSET_SOURCES:
        ai.create_asset({"name": "x", "assetType": "VENDOR", "source": source})
        ai.put_asset_by_external_id("e", {"name": "x", "assetType": "VENDOR", "source": source})
    assert post.call_count == put.call_count == 3


@respx.mock
def test_delete_asset_by_external_id_returns_outcome_body():
    route = respx.delete(f"{ASSETS}/by-external-id/ext-asset-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "a",
                "externalId": "ext-asset-1",
                "created": False,
                "changed": True,
                "updatedAt": "2026-09-22T00:00:01Z",
                "resource": {"id": "a", "externalId": "ext-asset-1", "archivedAt": "2026-09-22T00:00:01Z"},
            },
        )
    )
    result = _client().ai_systems.delete_asset_by_external_id("ext-asset-1")
    assert result["changed"] is True
    assert route.called


@respx.mock
def test_put_relationship_by_external_id_contract_and_enum_validation():
    route = respx.put(f"{RELATIONSHIPS}/by-external-id/ext-rel-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "r",
                "externalId": "ext-rel-1",
                "created": True,
                "changed": True,
                "updatedAt": "2026-09-22T00:00:00Z",
                "resource": {"id": "r", "externalId": "ext-rel-1", "relationshipType": "USES"},
            },
        )
    )
    result = _client().ai_systems.put_relationship_by_external_id(
        "ext-rel-1",
        {"sourceAssetId": "a", "targetAssetId": "b", "relationshipType": "USES"},
    )
    assert result["created"] is True
    assert route.called
    with pytest.raises(ValueError):
        _client().ai_systems.put_relationship_by_external_id(
            "ext-rel-1",
            {"sourceAssetId": "a", "targetAssetId": "b", "relationshipType": "NOT_A_TYPE"},
        )


@respx.mock
def test_delete_relationship_by_external_id_returns_outcome_body():
    route = respx.delete(f"{RELATIONSHIPS}/by-external-id/ext-rel-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "r",
                "externalId": "ext-rel-1",
                "created": False,
                "changed": True,
                "updatedAt": "2026-09-22T00:00:01Z",
                "resource": {"id": "r", "externalId": "ext-rel-1", "archivedAt": "2026-09-22T00:00:01Z"},
            },
        )
    )
    result = _client().ai_systems.delete_relationship_by_external_id("ext-rel-1")
    assert result["changed"] is True
    assert route.called
