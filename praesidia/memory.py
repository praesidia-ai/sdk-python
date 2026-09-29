"""
Praesidia SDK — MemoryResource (H2-06e).

Covers the org-scoped agent-memory & knowledge-store API
(``/organizations/{org_id}/memories``). Writes are PII-redacted +
poisoning-scanned and encrypted per-org on the backend; reads are decrypted for
authorized org readers and surface provenance + guardrail metadata per hit.
"""

from __future__ import annotations

import builtins
import re
from typing import Any

from ._http import HttpClient, path_segment


class MemoryResource:
    """
    Manage agent memories within an organisation.

    Endpoint base: ``/organizations/{org_id}/memories``

    Requires the ``AGENT_MEMORY`` feature and the ``MEMORY_CREATE`` /
    ``MEMORY_VIEW`` / ``MEMORY_ERASE`` / ``MEMORY_DELETE`` permissions.

    Example::

        m = client.memory.create(content="The customer prefers email.")
        hits = client.memory.search(query="contact preference", top_k=5)
    """

    # Exact backend enum values (`MemorySourceType` /
    # `MemoryRetentionRegime`). Keep these uppercase: class-validator rejects
    # the formerly documented lowercase spellings under forbidNonWhitelisted.
    SOURCE_TYPES = ("AGENT", "USER", "SYSTEM", "IMPORT")
    RETENTION_REGIMES = ("NONE", "GDPR", "HIPAA", "SOC2", "CUSTOM")

    def __init__(self, http: HttpClient) -> None:
        self._http = http
        self._base = f"/organizations/{http.org_id}/memories"

    def synchronize_source(self, *, source_reference: str, content_version: str,
                           allowed_user_ids: list[str], valid_until: str,
                           authority_url: str, authority_public_key: str,
                           state: str, expected_revision: int) -> dict[str, Any]:
        """Synchronize a connector-owned document ACL, version or revocation.

        Lease validity must come from a current upstream authorization check and
        cannot exceed 15 minutes. A deleted source is terminal.
        """
        return self._http.post(
            f"/organizations/{self._http.org_id}/memory-sources/synchronize",
            json={"sourceReference": source_reference, "contentVersion": content_version,
                  "authorityUrl": authority_url, "authorityPublicKey": authority_public_key,
                  "allowedUserIds": allowed_user_ids, "validUntil": valid_until,
                  "state": state, "expectedRevision": expected_revision},
        )

    def list_sources(self) -> list[dict[str, Any]]:
        """Return the latest 100 source authorizations owned by this connector user."""
        return self._http.get(self._base.removesuffix("memories") + "memory-sources")

    def create(
        self,
        content: str,
        *,
        subject_id: str | None = None,
        memory_key: str | None = None,
        tags: list[str] | None = None,
        source_type: str | None = None,
        source_agent_id: str | None = None,
        source_reference: str | None = None,
        access_source_id: str | None = None,
        retention_regime: str | None = None,
        retention_days: int | None = None,
    ) -> dict[str, Any]:
        """
        Write a memory (CreateMemoryDto). ``POST .../memories`` (MEMORY_CREATE).

        The content is PII-redacted + poisoning-scanned and encrypted per-org
        before persistence. Pass ``subject_id`` to bind the memory to a data
        subject so a later :meth:`erase` can GDPR Art-17 crypto-shred exactly
        that subject.

        Args:
            content:          Memory content to store.
            subject_id:       Opaque data-subject id (enables per-subject erase).
            memory_key:       Logical grouping key (namespace / conversation id).
            tags:             Free-form tags for retrieval filtering.
            source_type:      Provenance principal kind
                              (``AGENT`` | ``USER`` | ``SYSTEM`` | ``IMPORT``).
            source_agent_id:  Provenance: the agent that produced this memory.
            source_reference: Provenance: free-form origin ref (task id, url).
            retention_regime: Compliance retention regime
                              (``NONE`` | ``GDPR`` | ``HIPAA`` | ``SOC2`` | ``CUSTOM``).
            retention_days:   Custom window in days (only when regime=``CUSTOM``).

        Returns:
            The created memory dict.
        """
        if not isinstance(content, str) or not content or len(content) > 32_768:
            raise ValueError(
                "content must be a non-empty string of at most 32768 characters"
            )
        if source_type == "IMPORT" and not access_source_id:
            raise ValueError("Imported memory requires access_source_id")
        if source_type is not None and source_type not in self.SOURCE_TYPES:
            raise ValueError(
                f"source_type must be one of {self.SOURCE_TYPES}; got {source_type!r}"
            )
        if (
            retention_regime is not None
            and retention_regime not in self.RETENTION_REGIMES
        ):
            raise ValueError(
                "retention_regime must be one of "
                f"{self.RETENTION_REGIMES}; got {retention_regime!r}"
            )
        if retention_days is not None and (
            isinstance(retention_days, bool)
            or not isinstance(retention_days, int)
            or not 1 <= retention_days <= 36_500
        ):
            raise ValueError("retention_days must be an integer from 1 to 36500")
        if retention_regime == "CUSTOM" and retention_days is None:
            raise ValueError(
                "retention_days is required when retention_regime is 'CUSTOM'"
            )
        if retention_days is not None and retention_regime != "CUSTOM":
            raise ValueError(
                "retention_days is only valid when retention_regime is 'CUSTOM'"
            )

        payload: dict[str, Any] = {"content": content}
        if subject_id is not None:
            payload["subjectId"] = subject_id
        if memory_key is not None:
            payload["memoryKey"] = memory_key
        if tags is not None:
            payload["tags"] = tags
        if source_type is not None:
            payload["sourceType"] = source_type
        if source_agent_id is not None:
            payload["sourceAgentId"] = source_agent_id
        if access_source_id is not None:
            payload["accessSourceId"] = access_source_id
        if source_reference is not None:
            payload["sourceReference"] = source_reference
        if retention_regime is not None:
            payload["retentionRegime"] = retention_regime
        if retention_days is not None:
            payload["retentionDays"] = retention_days
        return self._http.post(self._base, json=payload)

    def list(
        self,
        *,
        page: int = 1,
        limit: int = 20,
        memory_key: str | None = None,
        source_type: str | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        List memories (org-scoped, paginated, decrypted). ``GET .../memories``
        (MEMORY_VIEW).

        Returns:
            A list of memory dicts (unwrapped from the pagination envelope).
        """
        if source_type is not None and source_type not in self.SOURCE_TYPES:
            raise ValueError(
                f"source_type must be one of {self.SOURCE_TYPES}; got {source_type!r}"
            )
        params: dict[str, Any] = {"page": page, "limit": limit}
        if memory_key is not None:
            params["memoryKey"] = memory_key
        if source_type is not None:
            params["sourceType"] = source_type
        if tag is not None:
            params["tag"] = tag
        result = self._http.get(self._base, params=params)
        if isinstance(result, list):
            return result
        return result.get("data", result.get("memories", []))

    def search(
        self,
        query: str,
        *,
        memory_key: str | None = None,
        top_k: int | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """
        Relevance search over memories (provenance surfaced per hit).
        ``POST .../memories/search`` (MEMORY_VIEW).

        Args:
            query:      Query text to match stored memories against.
            memory_key: Restrict search to a logical grouping key.
            top_k:      Max results to return (1..50, default server-side 10).

        Returns:
            A list of matching memory dicts.
        """
        if not isinstance(query, str) or not query or len(query) > 4_096:
            raise ValueError(
                "query must be a non-empty string of at most 4096 characters"
            )
        if top_k is not None and (
            isinstance(top_k, bool)
            or not isinstance(top_k, int)
            or not 1 <= top_k <= 50
        ):
            raise ValueError("top_k must be an integer from 1 to 50")
        payload: dict[str, Any] = {"query": query}
        if memory_key is not None:
            payload["memoryKey"] = memory_key
        if top_k is not None:
            payload["topK"] = top_k
        return self._http.post(f"{self._base}/search", json=payload)

    def erase(
        self,
        subject_id: str,
        reason: str,
        expected_subject_hash: str | None = None,
        acknowledge_cross_org: bool | None = None,
    ) -> dict[str, Any]:
        """
        Request a two-person GDPR Art-17 erasure of a data subject's memories.
        ``POST .../memories/erase`` (MEMORY_ERASE).

        This only *files* the request: nothing is destroyed and no erasure
        certificate exists yet. The backend answers ``202`` with a PENDING
        ``DATA_SUBJECT_ERASE`` approval; the crypto-shred (DEK destroy +
        certificate) happens only when a different system admin confirms it.

        Args:
            subject_id: The data-subject identifier whose memories to erase.
            reason:     Reason for erasure (recorded on the erasure certificate).
            expected_subject_hash: Optional server-issued subject HMAC
                (64 lowercase hex, e.g. from a prior erasure receipt). Omit it
                and the server derives it from ``subject_id``; if given it must
                match, else the API returns 400 ``subject_hash_mismatch``.
            acknowledge_cross_org: Set ``True`` to acknowledge that erasing a
                shared platform user may affect other organisations.

        Returns:
            The pending ApprovalRequest dict as the API returns it (``id``,
            ``status``, ``operationType``, ``expiresAt``, ...).

        Raises:
            ValueError: ``expected_subject_hash`` is not 64 lowercase hex, or
                ``acknowledge_cross_org`` is not a bool (before any request).
        """
        payload: dict[str, Any] = {"subjectId": subject_id, "reason": reason}
        if expected_subject_hash is not None:
            if not isinstance(expected_subject_hash, str) or not re.fullmatch(
                r"[a-f0-9]{64}", expected_subject_hash
            ):
                raise ValueError(
                    "expected_subject_hash must be 64 lowercase hex characters"
                )
            payload["expectedSubjectHash"] = expected_subject_hash
        if acknowledge_cross_org is not None:
            if not isinstance(acknowledge_cross_org, bool):
                raise ValueError("acknowledge_cross_org must be a bool")
            payload["acknowledgeCrossOrg"] = acknowledge_cross_org
        return self._http.post(f"{self._base}/erase", json=payload)

    def get(self, memory_id: str) -> dict[str, Any]:
        """
        Fetch a single memory (org-scoped, decrypted). ``GET .../memories/{id}``
        (MEMORY_VIEW).
        """
        return self._http.get(f"{self._base}/{path_segment(memory_id, 'memory_id')}")

    def delete(self, memory_id: str) -> None:
        """
        Soft-delete a single memory (org-scoped). ``DELETE .../memories/{id}``
        (MEMORY_DELETE). The backend answers 204 No Content.
        """
        self._http.delete(f"{self._base}/{path_segment(memory_id, 'memory_id')}")
