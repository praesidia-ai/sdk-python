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

    Not covered (out of SDK-0001/SDK-0002's scope, all exist on be's
    contract): AI System ``owners``/``lifecycle`` PATCH sub-routes, AI
    System ``delete`` (soft-delete), direct AI Asset ``create``/``update``/
    ``archive``/``restore``, multi-hop graph ``traverse`` (AISYS-0003, not
    yet on ``be/openapi.json``), and per-relationship ``get``/``update``/
    ``archive``/``restore``/role-change.
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
    #: `entities/ai-asset.entity.ts`'s `AI_ASSET_TYPES` (20 values).
    ASSET_TYPES = (
        "APPLICATION", "AGENT", "MODEL", "MODEL_ENDPOINT", "MCP_SERVER",
        "MCP_TOOL", "A2A_ENDPOINT", "API", "DATA_SOURCE", "DATASET",
        "VECTOR_STORE", "RAG_INDEX", "PROMPT", "SKILL", "VENDOR",
        "IDENTITY", "CREDENTIAL", "REPOSITORY", "CLOUD_RESOURCE", "WORKFLOW",
    )
    #: `entities/ai-asset.entity.ts`'s `AI_ASSET_SOURCES`.
    ASSET_SOURCES = ("manual", "runtime_observation", "discovery_connector", "api", "import")
    #: `entities/ai-asset.entity.ts`'s `AI_ASSET_DISCOVERY_STATUSES`.
    DISCOVERY_STATUSES = ("discovered", "adopted", "ignored")
    #: `entities/asset-relationship.entity.ts`'s `ASSET_RELATIONSHIP_TYPES`.
    RELATIONSHIP_TYPES = (
        "USES", "CALLS", "ACCESSES", "CONTAINS", "DELEGATES_TO", "HOSTED_BY",
        "READS", "HAS_PERMISSION", "GOVERNED_BY",
    )

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
