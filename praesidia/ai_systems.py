"""
Praesidia SDK — AiSystemsResource.

Covers the ``/organizations/{org_id}/ai-systems``, ``/ai-assets`` and
``/asset-relationships`` endpoints (be's AISYS-0002 module): the AI System
inventory, the AI Asset catalog (agents, models, MCP servers, data sources,
...), the membership linking assets to systems, and the relationship graph
(edges) between assets.

SDK-0002 — Python parity with the TypeScript SDK's ``PraesidiaAiSystems``
(``sdk/src/ai-systems.ts``, SDK-0001). Same method set, same documented
exclusions (see the class docstring below) -- no widening past SDK-0001's
scope.
"""

from __future__ import annotations

from typing import Any, Iterator

from ._http import HttpClient, path_segment
from ._pagination import normalize_paged_envelope, paginate_all


def _query(page: int, limit: int, **filters: Any) -> dict[str, Any]:
    """Build a params dict: ``page``/``limit`` plus any non-``None`` filter.

    A ``bool`` filter is sent as the lowercase string ``"true"``/``"false"``
    (matching JS's ``String(true)``) because be's list DTOs declare these
    fields ``@IsBooleanString`` (``includeArchived``), not a real boolean --
    Python's default ``str(True) == "True"`` would 400.
    """
    params: dict[str, Any] = {"page": page, "limit": limit}
    for key, value in filters.items():
        if value is None:
            continue
        if isinstance(value, bool):
            value = "true" if value else "false"
        params[key] = value
    return params


class AiSystemsResource:
    """
    Manage the AI System inventory, AI Asset catalog and asset relationship
    graph within an organisation.

    Endpoint bases: ``/organizations/{org_id}/ai-systems``, ``/ai-assets``,
    ``/asset-relationships``. Requires the ``AI_SYSTEMS`` feature and the
    ``AI_SYSTEMS_*`` / ``AI_ASSETS_*`` permission families.

    SDK-0004 adds AI System ``owners``/``lifecycle`` PATCH sub-routes,
    ``delete`` (soft-delete), direct AI Asset ``create``/``get``/``update``/
    ``archive``/``restore``, membership ``change_asset_role``, and
    per-relationship ``get``/``update``/``archive``/``restore``.

    SDK-0006 adds the multi-hop graph :meth:`traverse` (AISYS-0003) and the
    AI System :meth:`summary` aggregation (AISYS-0004), now both on
    ``ui/swagger.json``.

    SDK-0302 (PRAE-228/229) adds the declarative ``by-external-id``
    desired-state methods (``put_system_by_external_id`` and its asset/
    relationship siblings, be's BE-0579) -- the shape IaC tooling (Terraform
    provider, k8s operator) needs.
    """

    #: `entities/ai-system.entity.ts`'s `AI_SYSTEM_CRITICALITIES`.
    CRITICALITIES = ("low", "medium", "high", "critical")
    #: `entities/ai-system.entity.ts`'s `AI_SYSTEM_ENVIRONMENTS`.
    ENVIRONMENTS = ("development", "staging", "production", "sandbox")
    #: `entities/ai-system.entity.ts`'s `AI_SYSTEM_LIFECYCLE_STATUSES`.
    LIFECYCLE_STATUSES = (
        "proposed",
        "assessment",
        "approved",
        "development",
        "production",
        "suspended",
        "retired",
    )
    #: `entities/ai-asset.entity.ts`'s `AI_ASSET_TYPES` (23 values, SDK-0007
    #: synced with DB-0300's widened enum; kept in sync via
    #: `tests/test_ai_systems.py::test_asset_and_relationship_types_match_openapi`).
    ASSET_TYPES = (
        "APPLICATION", "AGENT", "MODEL", "MODEL_ENDPOINT", "MCP_SERVER",
        "MCP_TOOL", "A2A_ENDPOINT", "API", "DATA_SOURCE", "DATASET",
        "VECTOR_STORE", "RAG_INDEX", "PROMPT", "SKILL", "VENDOR",
        "IDENTITY", "CREDENTIAL", "REPOSITORY", "CLOUD_RESOURCE", "WORKFLOW",
        "TOOL", "API_ENDPOINT", "DATA_SCOPE",
    )
    #: `entities/ai-asset.entity.ts`'s `AI_ASSET_SOURCES`.
    ASSET_SOURCES = ("manual", "runtime_observation", "discovery_connector", "api", "import")
    #: `entities/ai-asset.entity.ts`'s `AI_ASSET_DISCOVERY_STATUSES`.
    DISCOVERY_STATUSES = ("discovered", "adopted", "ignored")
    #: `entities/asset-relationship.entity.ts`'s `ASSET_RELATIONSHIP_TYPES`
    #: (13 values, SDK-0007/SDK-0304 synced -- see `ASSET_TYPES` note above).
    RELATIONSHIP_TYPES = (
        "USES", "CALLS", "ACCESSES", "CONTAINS", "DELEGATES_TO", "HOSTED_BY",
        "READS", "WRITES", "HAS_PERMISSION", "GOVERNED_BY", "CAN_INVOKE",
        "GRANTS_SCOPE", "CAN_ASSUME",
    )
    #: `TraverseAssetGraphQueryDto`'s `direction` enum (AISYS-0003).
    TRAVERSE_DIRECTIONS = ("downstream", "upstream", "both")

    def __init__(self, http: HttpClient) -> None:
        self._http = http
        self._systems_base = f"/organizations/{http.org_id}/ai-systems"
        self._assets_base = f"/organizations/{http.org_id}/ai-assets"
        self._relationships_base = f"/organizations/{http.org_id}/asset-relationships"

    # ------------------------------------------------------------------
    # AI Systems
    # ------------------------------------------------------------------

    def list(
        self,
        *,
        lifecycle_status: str | None = None,
        environment: str | None = None,
        criticality: str | None = None,
        business_unit: str | None = None,
        owner_id: str | None = None,
        include_archived: bool | None = None,
        q: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Return a paginated list of AI Systems for the organisation.

        SCAN2-011 -- returns only the requested page. Use :meth:`list_page`
        for the full envelope or :meth:`list_all` to auto-paginate through
        every AI System.
        """
        return self.list_page(
            lifecycle_status=lifecycle_status,
            environment=environment,
            criticality=criticality,
            business_unit=business_unit,
            owner_id=owner_id,
            include_archived=include_archived,
            q=q,
            page=page,
            limit=limit,
        )["data"]

    def list_page(
        self,
        *,
        lifecycle_status: str | None = None,
        environment: str | None = None,
        criticality: str | None = None,
        business_unit: str | None = None,
        owner_id: str | None = None,
        include_archived: bool | None = None,
        q: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Like :meth:`list`, but returns be's full pagination envelope (SCAN2-011)."""
        if lifecycle_status is not None and lifecycle_status not in self.LIFECYCLE_STATUSES:
            raise ValueError(f"lifecycle_status must be one of {self.LIFECYCLE_STATUSES}; got {lifecycle_status!r}")
        if environment is not None and environment not in self.ENVIRONMENTS:
            raise ValueError(f"environment must be one of {self.ENVIRONMENTS}; got {environment!r}")
        if criticality is not None and criticality not in self.CRITICALITIES:
            raise ValueError(f"criticality must be one of {self.CRITICALITIES}; got {criticality!r}")
        params = _query(
            page,
            limit,
            lifecycleStatus=lifecycle_status,
            environment=environment,
            criticality=criticality,
            businessUnit=business_unit,
            ownerId=owner_id,
            includeArchived=include_archived,
            q=q,
        )
        result = self._http.get(self._systems_base, params=params)
        return normalize_paged_envelope(result, "aiSystems")

    def list_all(
        self,
        *,
        lifecycle_status: str | None = None,
        environment: str | None = None,
        criticality: str | None = None,
        business_unit: str | None = None,
        owner_id: str | None = None,
        include_archived: bool | None = None,
        q: str | None = None,
        limit: int = 20,
    ) -> Iterator[dict[str, Any]]:
        """Auto-paginate through every AI System, across every page (SCAN2-011)."""
        yield from paginate_all(
            lambda page: self.list_page(
                lifecycle_status=lifecycle_status,
                environment=environment,
                criticality=criticality,
                business_unit=business_unit,
                owner_id=owner_id,
                include_archived=include_archived,
                q=q,
                page=page,
                limit=limit,
            )
        )

    def get(self, ai_system_id: str) -> dict[str, Any]:
        """Fetch a single AI System by ID. GET .../ai-systems/:id."""
        return self._http.get(f"{self._systems_base}/{path_segment(ai_system_id, 'ai_system_id')}")

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        """Create a new AI System. POST .../ai-systems."""
        return self._http.post(self._systems_base, json=data)

    def update(self, ai_system_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """Partially update an AI System. PATCH .../ai-systems/:id."""
        return self._http.patch(
            f"{self._systems_base}/{path_segment(ai_system_id, 'ai_system_id')}", json=data
        )

    def archive(self, ai_system_id: str) -> dict[str, Any]:
        """Archive an AI System. POST .../ai-systems/:id/archive."""
        return self._http.post(
            f"{self._systems_base}/{path_segment(ai_system_id, 'ai_system_id')}/archive", json={}
        )

    def restore(self, ai_system_id: str) -> dict[str, Any]:
        """Restore an archived AI System. POST .../ai-systems/:id/restore."""
        return self._http.post(
            f"{self._systems_base}/{path_segment(ai_system_id, 'ai_system_id')}/restore", json={}
        )

    def update_owners(self, ai_system_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """
        Update any subset of the four owner pairs. PATCH .../ai-systems/:id/owners.

        Args:
            data: any of ``ownerType``/``ownerId``, ``technicalOwnerType``/
                  ``technicalOwnerId``, ``securityOwnerType``/``securityOwnerId``,
                  ``complianceOwnerType``/``complianceOwnerId`` (``UpdateAiSystemOwnersDto``,
                  each ``*Type`` in :attr:`OWNER_TYPES`; ``None`` clears a pair).
        """
        return self._http.patch(
            f"{self._systems_base}/{path_segment(ai_system_id, 'ai_system_id')}/owners", json=data
        )

    def transition_lifecycle(self, ai_system_id: str, lifecycle_status: str) -> dict[str, Any]:
        """
        Move an AI System to a new lifecycle status. PATCH .../ai-systems/:id/lifecycle.

        Raises:
            ValueError: ``lifecycle_status`` is not in :attr:`LIFECYCLE_STATUSES`.

        be 400s with ``Invalid AI System lifecycle transition: '<from>' -> '<to>'``
        on a structurally valid but illegal move (e.g. ``retired`` -> ``production``).
        """
        if lifecycle_status not in self.LIFECYCLE_STATUSES:
            raise ValueError(
                f"lifecycle_status must be one of {self.LIFECYCLE_STATUSES}; got {lifecycle_status!r}"
            )
        return self._http.patch(
            f"{self._systems_base}/{path_segment(ai_system_id, 'ai_system_id')}/lifecycle",
            json={"lifecycleStatus": lifecycle_status},
        )

    def delete(self, ai_system_id: str) -> None:
        """Soft-delete an AI System (sets ``deletedAt``). DELETE .../ai-systems/:id."""
        self._http.delete(f"{self._systems_base}/{path_segment(ai_system_id, 'ai_system_id')}")

    def put_system_by_external_id(self, external_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """
        Declaratively create-or-update an AI System keyed by an
        externally-owned ``external_id`` (be's BE-0579 desired-state API,
        SDK-0302/PRAE-228/229) -- the shape IaC tooling (Terraform provider,
        k8s operator) needs instead of a lookup-then-create/update round
        trip. PUT .../ai-systems/by-external-id/:externalId.

        Idempotent: the same ``data`` sent twice returns ``changed: False``
        the second time with an unchanged ``updatedAt`` -- check it before
        assuming a write happened.

        Returns:
            ``{"id", "externalId", "created", "changed", "updatedAt",
            "resource"}`` (``DesiredStateOutcomeDto``).
        """
        return self._http.put(
            f"{self._systems_base}/by-external-id/{path_segment(external_id, 'external_id')}",
            json=data,
        )

    def delete_system_by_external_id(self, external_id: str) -> dict[str, Any]:
        """
        Archive the AI System matching ``external_id`` (never a hard delete,
        same as :meth:`archive`). DELETE .../ai-systems/by-external-id/:externalId.
        Another tenant's ``external_id`` 404s rather than leaking existence.
        """
        return self._http.delete_returning(
            f"{self._systems_base}/by-external-id/{path_segment(external_id, 'external_id')}"
        )

    def summary(self, ai_system_id: str) -> dict[str, Any]:
        """
        Thin cross-section aggregation for one AI System.
        GET .../ai-systems/:id/summary (AISYS-0004).

        Returns ``{"compliance", "risk", "evaluations", "cost", "evidence":
        {"available": bool, "reason"?, "counts"?, "updatedAt"?}, "unlinkedAssets":
        int}`` (``AiSystemSummaryResponseDto``). A section's ``available`` is
        ``False`` (with a ``reason``) when be cannot filter that section by
        this system's asset entity ids at all -- not necessarily an empty
        result.
        """
        return self._http.get(
            f"{self._systems_base}/{path_segment(ai_system_id, 'ai_system_id')}/summary"
        )

    # ------------------------------------------------------------------
    # AI Assets
    # ------------------------------------------------------------------

    def list_assets(
        self,
        *,
        asset_type: str | None = None,
        source: str | None = None,
        discovery_status: str | None = None,
        environment: str | None = None,
        include_archived: bool | None = None,
        q: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Return a paginated list of AI Assets for the organisation.

        SCAN2-011 -- returns only the requested page. Use
        :meth:`list_assets_page` for the full envelope or
        :meth:`list_assets_all` to auto-paginate through every asset.
        """
        return self.list_assets_page(
            asset_type=asset_type,
            source=source,
            discovery_status=discovery_status,
            environment=environment,
            include_archived=include_archived,
            q=q,
            page=page,
            limit=limit,
        )["data"]

    def list_assets_page(
        self,
        *,
        asset_type: str | None = None,
        source: str | None = None,
        discovery_status: str | None = None,
        environment: str | None = None,
        include_archived: bool | None = None,
        q: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Like :meth:`list_assets`, but returns be's full pagination envelope (SCAN2-011)."""
        if asset_type is not None and asset_type not in self.ASSET_TYPES:
            raise ValueError(f"asset_type must be one of {self.ASSET_TYPES}; got {asset_type!r}")
        if source is not None and source not in self.ASSET_SOURCES:
            raise ValueError(f"source must be one of {self.ASSET_SOURCES}; got {source!r}")
        if discovery_status is not None and discovery_status not in self.DISCOVERY_STATUSES:
            raise ValueError(f"discovery_status must be one of {self.DISCOVERY_STATUSES}; got {discovery_status!r}")
        if environment is not None and environment not in self.ENVIRONMENTS:
            raise ValueError(f"environment must be one of {self.ENVIRONMENTS}; got {environment!r}")
        params = _query(
            page,
            limit,
            assetType=asset_type,
            source=source,
            discoveryStatus=discovery_status,
            environment=environment,
            includeArchived=include_archived,
            q=q,
        )
        result = self._http.get(self._assets_base, params=params)
        return normalize_paged_envelope(result, "aiAssets")

    def list_assets_all(
        self,
        *,
        asset_type: str | None = None,
        source: str | None = None,
        discovery_status: str | None = None,
        environment: str | None = None,
        include_archived: bool | None = None,
        q: str | None = None,
        limit: int = 20,
    ) -> Iterator[dict[str, Any]]:
        """Auto-paginate through every AI Asset, across every page (SCAN2-011)."""
        yield from paginate_all(
            lambda page: self.list_assets_page(
                asset_type=asset_type,
                source=source,
                discovery_status=discovery_status,
                environment=environment,
                include_archived=include_archived,
                q=q,
                page=page,
                limit=limit,
            )
        )

    def create_asset(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Create an AI Asset with no backing runtime entity (e.g. ``VENDOR``,
        ``CREDENTIAL``, ``MODEL_ENDPOINT`` — discovered/tracked metadata
        only). POST .../ai-assets. To register an asset that IS a real
        agent/application/MCP server/model/workflow/eval dataset row, use
        :meth:`adopt_asset` instead.

        Args:
            data: ``{"name": ..., "assetType": ..., "source"?: ...,
                   "discoveryStatus"?: ..., "environment"?: ...,
                   "ownerType"?: ..., "ownerId"?: ..., "metadata"?: ...}``
                  (``CreateAiAssetDto``).
        """
        return self._http.post(self._assets_base, json=data)

    def get_asset(self, asset_id: str) -> dict[str, Any]:
        """Fetch a single AI Asset by ID. GET .../ai-assets/:id."""
        return self._http.get(f"{self._assets_base}/{path_segment(asset_id, 'asset_id')}")

    def update_asset(self, asset_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """
        Partially update an AI Asset's descriptive fields. PATCH .../ai-assets/:id.
        ``assetType``/``source``/``discoveryStatus`` are immutable/behaviour-owned
        and not accepted here (``UpdateAiAssetDto``).
        """
        return self._http.patch(f"{self._assets_base}/{path_segment(asset_id, 'asset_id')}", json=data)

    def archive_asset(self, asset_id: str) -> dict[str, Any]:
        """Archive an AI Asset. POST .../ai-assets/:id/archive."""
        return self._http.post(f"{self._assets_base}/{path_segment(asset_id, 'asset_id')}/archive", json={})

    def restore_asset(self, asset_id: str) -> dict[str, Any]:
        """Restore an archived AI Asset. POST .../ai-assets/:id/restore."""
        return self._http.post(f"{self._assets_base}/{path_segment(asset_id, 'asset_id')}/restore", json={})

    def adopt_asset(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Adopt an existing entity (agent, application, MCP server, ...) into
        the AI Asset inventory. POST .../ai-assets/adopt. Idempotent: a
        repeat call for the same ``entityType``/``entityId`` returns the
        same asset, no duplicate.

        Args:
            data: ``{"entityType": ..., "entityId": ..., "aiSystemId"?: ...,
                   "role"?: ...}`` (``AdoptAiAssetDto``).
        """
        return self._http.post(f"{self._assets_base}/adopt", json=data)

    def put_asset_by_external_id(self, external_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """
        Declaratively create-or-update an AI Asset keyed by an
        externally-owned ``external_id`` (be's BE-0579, SDK-0302/PRAE-228/229).
        PUT .../ai-assets/by-external-id/:externalId. Idempotent -- see
        :meth:`put_system_by_external_id`.

        Args:
            data: ``CreateAiAssetDto`` shape (``assetType`` validated against
                  :attr:`ASSET_TYPES`, ``source``/``discoveryStatus`` against
                  their own tuples, matching :meth:`list_assets_page`).
        """
        if (asset_type := data.get("assetType")) is not None and asset_type not in self.ASSET_TYPES:
            raise ValueError(f"assetType must be one of {self.ASSET_TYPES}; got {asset_type!r}")
        if (source := data.get("source")) is not None and source not in self.ASSET_SOURCES:
            raise ValueError(f"source must be one of {self.ASSET_SOURCES}; got {source!r}")
        if (status := data.get("discoveryStatus")) is not None and status not in self.DISCOVERY_STATUSES:
            raise ValueError(f"discoveryStatus must be one of {self.DISCOVERY_STATUSES}; got {status!r}")
        return self._http.put(
            f"{self._assets_base}/by-external-id/{path_segment(external_id, 'external_id')}",
            json=data,
        )

    def delete_asset_by_external_id(self, external_id: str) -> dict[str, Any]:
        """
        Archive the AI Asset matching ``external_id`` (never a hard delete).
        DELETE .../ai-assets/by-external-id/:externalId.
        """
        return self._http.delete_returning(
            f"{self._assets_base}/by-external-id/{path_segment(external_id, 'external_id')}"
        )

    # ------------------------------------------------------------------
    # AI System <-> Asset membership
    # ------------------------------------------------------------------

    def attach_asset(self, ai_system_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """
        Attach an AI Asset to an AI System. POST .../ai-systems/:id/assets.

        Args:
            ai_system_id: UUID of the AI System.
            data:         ``{"assetId": ..., "role"?: ...}``
                          (``AttachAiSystemAssetDto``).
        """
        return self._http.post(
            f"{self._systems_base}/{path_segment(ai_system_id, 'ai_system_id')}/assets", json=data
        )

    def detach_asset(self, ai_system_id: str, asset_id: str) -> None:
        """Detach an AI Asset from an AI System. DELETE .../ai-systems/:id/assets/:assetId."""
        self._http.delete(
            f"{self._systems_base}/{path_segment(ai_system_id, 'ai_system_id')}"
            f"/assets/{path_segment(asset_id, 'asset_id')}"
        )

    def change_asset_role(self, ai_system_id: str, asset_id: str, role: str) -> dict[str, Any]:
        """
        Change an attached AI Asset's role on an AI System.
        PATCH .../ai-systems/:id/assets/:assetId/role.

        Args:
            role: ``primary``/``supporting``/``dependency``/``external``
                  (``AiSystemAssetRole`` — not client-side validated, matching
                  :meth:`attach_asset`; be 400s on an unknown value).
        """
        return self._http.patch(
            f"{self._systems_base}/{path_segment(ai_system_id, 'ai_system_id')}"
            f"/assets/{path_segment(asset_id, 'asset_id')}/role",
            json={"role": role},
        )

    # ------------------------------------------------------------------
    # Asset relationships (graph edges)
    # ------------------------------------------------------------------

    def create_relationship(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a relationship (edge) between two AI Assets.
        POST .../asset-relationships.

        Args:
            data: ``{"sourceAssetId": ..., "targetAssetId": ...,
                   "relationshipType": ..., "source"?: ..., "confidence"?:
                   ..., "metadata"?: ...}`` (``CreateAssetRelationshipDto``).
        """
        return self._http.post(self._relationships_base, json=data)

    def get_relationship(self, relationship_id: str) -> dict[str, Any]:
        """Fetch a single asset relationship by ID. GET .../asset-relationships/:id."""
        return self._http.get(
            f"{self._relationships_base}/{path_segment(relationship_id, 'relationship_id')}"
        )

    def update_relationship(self, relationship_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """
        Update a relationship's ``relationshipType``/``source``/``confidence``/
        ``metadata`` (not its endpoints -- move an endpoint by archiving this
        edge and creating a new one). PATCH .../asset-relationships/:id.
        Bumps ``version`` and appends an ``asset_relationship_history`` row.
        """
        return self._http.patch(
            f"{self._relationships_base}/{path_segment(relationship_id, 'relationship_id')}", json=data
        )

    def archive_relationship(self, relationship_id: str) -> dict[str, Any]:
        """Archive an asset relationship. POST .../asset-relationships/:id/archive."""
        return self._http.post(
            f"{self._relationships_base}/{path_segment(relationship_id, 'relationship_id')}/archive",
            json={},
        )

    def restore_relationship(self, relationship_id: str) -> dict[str, Any]:
        """Restore an archived asset relationship. POST .../asset-relationships/:id/restore."""
        return self._http.post(
            f"{self._relationships_base}/{path_segment(relationship_id, 'relationship_id')}/restore",
            json={},
        )

    def list_relationships(
        self,
        *,
        source_asset_id: str | None = None,
        target_asset_id: str | None = None,
        asset_id: str | None = None,
        relationship_type: str | None = None,
        include_archived: bool | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Return a paginated list of asset relationships for the organisation.

        SCAN2-011 -- returns only the requested page. Use
        :meth:`list_relationships_page` for the full envelope or
        :meth:`list_relationships_all` to auto-paginate through every
        relationship.

        Args:
            asset_id: Matches either endpoint (source or target).
        """
        return self.list_relationships_page(
            source_asset_id=source_asset_id,
            target_asset_id=target_asset_id,
            asset_id=asset_id,
            relationship_type=relationship_type,
            include_archived=include_archived,
            page=page,
            limit=limit,
        )["data"]

    def list_relationships_page(
        self,
        *,
        source_asset_id: str | None = None,
        target_asset_id: str | None = None,
        asset_id: str | None = None,
        relationship_type: str | None = None,
        include_archived: bool | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Like :meth:`list_relationships`, but returns be's full pagination envelope (SCAN2-011)."""
        if relationship_type is not None and relationship_type not in self.RELATIONSHIP_TYPES:
            raise ValueError(
                f"relationship_type must be one of {self.RELATIONSHIP_TYPES}; got {relationship_type!r}"
            )
        params = _query(
            page,
            limit,
            sourceAssetId=source_asset_id,
            targetAssetId=target_asset_id,
            assetId=asset_id,
            relationshipType=relationship_type,
            includeArchived=include_archived,
        )
        result = self._http.get(self._relationships_base, params=params)
        return normalize_paged_envelope(result, "relationships")

    def list_relationships_all(
        self,
        *,
        source_asset_id: str | None = None,
        target_asset_id: str | None = None,
        asset_id: str | None = None,
        relationship_type: str | None = None,
        include_archived: bool | None = None,
        limit: int = 20,
    ) -> Iterator[dict[str, Any]]:
        """Auto-paginate through every asset relationship, across every page (SCAN2-011)."""
        yield from paginate_all(
            lambda page: self.list_relationships_page(
                source_asset_id=source_asset_id,
                target_asset_id=target_asset_id,
                asset_id=asset_id,
                relationship_type=relationship_type,
                include_archived=include_archived,
                page=page,
                limit=limit,
            )
        )

    def traverse(
        self,
        asset_id: str,
        *,
        direction: str = "downstream",
        max_depth: int | None = None,
        asset_types: list[str] | None = None,
        relationship_types: list[str] | None = None,
        include_archived: bool | None = None,
    ) -> dict[str, Any]:
        """
        Multi-hop traversal of the asset relationship graph from an anchor
        asset. GET .../asset-relationships/graph/traverse (AISYS-0003).

        Args:
            asset_id: UUID of the anchor asset to traverse from.
            direction: one of :attr:`TRAVERSE_DIRECTIONS` (default
                       ``"downstream"``), client-side validated.
            max_depth: requested hop cap (server default 3; server-clamped
                       to ``AI_SYSTEM_GRAPH_MAX_DEPTH``, default 6 -- a
                       request above the cap is lowered, not rejected, and
                       ``stats.depthClamped`` reports it).
            asset_types: filter to these asset types (see :attr:`ASSET_TYPES`,
                         client-side validated as of SDK-0007 now that the
                         constant is kept in sync with be's enum -- see
                         :attr:`ASSET_TYPES`'s note), applied inside the
                         recursive leg -- a filtered-out node also prunes
                         everything beyond it.
            relationship_types: filter to these relationship types (see
                                 :attr:`RELATIONSHIP_TYPES`, client-side
                                 validated as of SDK-0007), same pruning
                                 behaviour.
            include_archived: include archived assets/relationships in the
                               traversal (server default ``False``).

        Returns:
            ``{"nodes": [...], "edges": [...], "stats": {...}}``
            (``AssetGraphTraversalResponseDto``).

        Raises:
            ValueError: ``direction`` is not one of :attr:`TRAVERSE_DIRECTIONS`,
                        or any entry of ``asset_types``/``relationship_types``
                        is not one of :attr:`ASSET_TYPES`/:attr:`RELATIONSHIP_TYPES`.

        be 404s if ``asset_id`` is not found in this org, and 413s if the
        traversal result exceeds ``AI_SYSTEM_GRAPH_MAX_NODES``.
        """
        if direction not in self.TRAVERSE_DIRECTIONS:
            raise ValueError(
                f"direction must be one of {self.TRAVERSE_DIRECTIONS}; got {direction!r}"
            )
        if asset_types:
            for asset_type in asset_types:
                if asset_type not in self.ASSET_TYPES:
                    raise ValueError(
                        f"asset_types entries must be one of {self.ASSET_TYPES}; got {asset_type!r}"
                    )
        if relationship_types:
            for relationship_type in relationship_types:
                if relationship_type not in self.RELATIONSHIP_TYPES:
                    raise ValueError(
                        f"relationship_types entries must be one of {self.RELATIONSHIP_TYPES}; "
                        f"got {relationship_type!r}"
                    )
        params: dict[str, Any] = {"assetId": asset_id, "direction": direction}
        if max_depth is not None:
            params["maxDepth"] = max_depth
        if asset_types:
            params["assetTypes"] = list(asset_types)
        if relationship_types:
            params["relationshipTypes"] = list(relationship_types)
        if include_archived is not None:
            params["includeArchived"] = "true" if include_archived else "false"
        return self._http.get(f"{self._relationships_base}/graph/traverse", params=params)

    def put_relationship_by_external_id(self, external_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """
        Declaratively create-or-update a relationship (edge) keyed by an
        externally-owned ``external_id`` (be's BE-0579, SDK-0302/PRAE-228/229).
        PUT .../asset-relationships/by-external-id/:externalId. Idempotent --
        see :meth:`put_system_by_external_id`.

        Args:
            data: ``CreateAssetRelationshipDto`` shape (``relationshipType``
                  validated against :attr:`RELATIONSHIP_TYPES`).
        """
        rel_type = data.get("relationshipType")
        if rel_type is not None and rel_type not in self.RELATIONSHIP_TYPES:
            raise ValueError(f"relationshipType must be one of {self.RELATIONSHIP_TYPES}; got {rel_type!r}")
        return self._http.put(
            f"{self._relationships_base}/by-external-id/{path_segment(external_id, 'external_id')}",
            json=data,
        )

    def delete_relationship_by_external_id(self, external_id: str) -> dict[str, Any]:
        """
        Archive the relationship matching ``external_id`` (never a hard
        delete). DELETE .../asset-relationships/by-external-id/:externalId.
        """
        return self._http.delete_returning(
            f"{self._relationships_base}/by-external-id/{path_segment(external_id, 'external_id')}"
        )
