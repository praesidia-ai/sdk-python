"""
Praesidia SDK — MemoryResource (H2-06e).

Covers the org-scoped agent-memory & knowledge-store API
(``/organizations/{org_id}/memories``). Writes are PII-redacted +
poisoning-scanned and encrypted per-org on the backend; reads are decrypted for
authorized org readers and surface provenance + guardrail metadata per hit.
"""

from __future__ import annotations

from typing import Any, Optional

from ._http import HttpClient


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

    def __init__(self, http: HttpClient) -> None:
        self._http = http
        self._base = f"/organizations/{http.org_id}/memories"

    def create(
        self,
        content: str,
        *,
        subject_id: Optional[str] = None,
        memory_key: Optional[str] = None,
        tags: Optional[list[str]] = None,
        source_type: Optional[str] = None,
        source_agent_id: Optional[str] = None,
        source_reference: Optional[str] = None,
        retention_regime: Optional[str] = None,
        retention_days: Optional[int] = None,
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
                              (``agent`` | ``user`` | ``system`` | ``tool`` | ``import``).
            source_agent_id:  Provenance: the agent that produced this memory.
            source_reference: Provenance: free-form origin ref (task id, url).
            retention_regime: Compliance retention regime
                              (``none`` | ``gdpr`` | ``hipaa`` | ``sox`` | ``custom``).
            retention_days:   Custom window in days (only when regime=custom).

        Returns:
            The created memory dict.
        """
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
        memory_key: Optional[str] = None,
        source_type: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """
        List memories (org-scoped, paginated, decrypted). ``GET .../memories``
        (MEMORY_VIEW).

        Returns:
            A list of memory dicts (unwrapped from the pagination envelope).
        """
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
        memory_key: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> list[dict[str, Any]]:
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
        payload: dict[str, Any] = {"query": query}
        if memory_key is not None:
            payload["memoryKey"] = memory_key
        if top_k is not None:
            payload["topK"] = top_k
        return self._http.post(f"{self._base}/search", json=payload)

    def erase(self, subject_id: str, reason: str) -> dict[str, Any]:
        """
        GDPR Art-17 crypto-shred a data subject's memories (DEK destroy +
        certificate). ``POST .../memories/erase`` (MEMORY_ERASE).

        Args:
            subject_id: The data-subject identifier whose memories to erase.
            reason:     Reason for erasure (recorded on the erasure certificate).

        Returns:
            A dict with ``subjectExternalIdHash``, ``memoriesErased``,
            ``dekDestroyed`` and ``certificateId`` (str or None).
        """
        return self._http.post(
            f"{self._base}/erase",
            json={"subjectId": subject_id, "reason": reason},
        )

    def get(self, memory_id: str) -> dict[str, Any]:
        """
        Fetch a single memory (org-scoped, decrypted). ``GET .../memories/{id}``
        (MEMORY_VIEW).
        """
        return self._http.get(f"{self._base}/{memory_id}")

    def delete(self, memory_id: str) -> None:
        """
        Soft-delete a single memory (org-scoped). ``DELETE .../memories/{id}``
        (MEMORY_DELETE). The backend answers 204 No Content.
        """
        self._http.delete(f"{self._base}/{memory_id}")
