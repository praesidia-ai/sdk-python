"""
Praesidia SDK — AgentsResource.

Covers the ``/organizations/{org_id}/agents`` and
``/organizations/{org_id}/tasks`` management endpoints.
"""

from __future__ import annotations

from typing import Any

from ._http import HttpClient


class AgentsResource:
    """
    Manage AI agents within an organisation.

    Endpoint base: ``/organizations/{org_id}/agents``
    """

    def __init__(self, http: HttpClient) -> None:
        self._http = http
        self._base = f"/organizations/{http.org_id}/agents"

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def list(self, page: int = 1, limit: int = 20) -> list[dict[str, Any]]:
        """
        Return a paginated list of agents for the organisation.

        Args:
            page:  1-based page number (default: 1).
            limit: Maximum results per page (default: 20).

        Returns:
            A list of agent dicts as returned by the API.
        """
        result = self._http.get(self._base, params={"page": page, "limit": limit})
        # The API may return a pagination envelope or a plain list.
        if isinstance(result, list):
            return result
        return result.get("data", result.get("agents", []))

    def get(self, agent_id: str) -> dict[str, Any]:
        """
        Fetch a single agent by ID.

        Args:
            agent_id: UUID of the agent to retrieve.

        Returns:
            Agent dict.

        Raises:
            NotFoundError: If no agent with that ID exists in the org.
        """
        return self._http.get(f"{self._base}/{agent_id}")

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a new agent.

        Args:
            data: Agent creation payload (name, type, model, etc.).

        Returns:
            Created agent dict.
        """
        return self._http.post(self._base, json=data)

    def update(self, agent_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """
        Partially update an agent.

        Args:
            agent_id: UUID of the agent to update.
            data:     Fields to update (only supplied fields are changed).

        Returns:
            Updated agent dict.
        """
        return self._http.patch(f"{self._base}/{agent_id}", json=data)

    def delete(self, agent_id: str) -> None:
        """
        Delete an agent.

        Args:
            agent_id: UUID of the agent to delete.
        """
        self._http.delete(f"{self._base}/{agent_id}")

    # ------------------------------------------------------------------
    # Credential rotation (Q4-01)
    # ------------------------------------------------------------------

    def rotate_client_secret(
        self,
        agent_id: str,
        grace_period_seconds: int | None = None,
    ) -> dict[str, Any]:
        """
        Rotate an agent's A2A client secret, minting a fresh plaintext secret.

        Calls ``POST .../agents/{agent_id}/client-secret/rotate`` (requires the
        ``AGENTS_CONFIGURE`` permission).

        Pass ``grace_period_seconds`` (0..604800) to keep the OUTGOING secret
        valid for a bounded overlap so live consumers can swap over with zero
        downtime; omit it (or pass ``0``) for an instant, fail-closed rotation
        that revokes the old secret immediately (the emergency/panic path).
        Distinct from the instant ``regenerate-secret`` endpoint, which has no
        grace window.

        Args:
            agent_id:             UUID of the agent whose secret to rotate.
            grace_period_seconds: Overlap window in seconds (0..604800). Omit
                                  or ``0`` for an instant rotation. Clamped
                                  server-side.

        Returns:
            A dict with ``clientId``, ``clientSecret`` (the NEW plaintext
            secret), ``graceEndsAt`` (ISO-8601 str or None) and
            ``gracePeriodSeconds`` (effective, after clamping).

        Security:
            ``clientSecret`` is shown EXACTLY ONCE — Praesidia stores only its
            hash. Persist it immediately (it is never recoverable) and never
            log it.
        """
        payload: dict[str, Any] = {}
        if grace_period_seconds is not None:
            payload["gracePeriodSeconds"] = grace_period_seconds
        return self._http.post(
            f"{self._base}/{agent_id}/client-secret/rotate", json=payload
        )

    def refresh_credential(self, api_key: str) -> None:
        """
        Adopt a rotated credential in-process, at runtime (zero-downtime swap).

        Call this after :meth:`rotate_client_secret` with the returned
        ``clientSecret`` (or any newly provisioned credential): subsequent
        requests authenticate with the new secret. Combined with the grace
        window returned by :meth:`rotate_client_secret`, the previous secret
        keeps working until ``graceEndsAt``, so no in-flight caller is rejected
        during the swap. Also reachable as ``client.refresh_credential(...)``.

        Security: the credential is held only in memory and is never logged.
        """
        self._http.set_api_key(api_key)

    # ------------------------------------------------------------------
    # Task submission
    # ------------------------------------------------------------------

    def run(
        self,
        agent_id: str,
        input: dict[str, Any],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """
        Submit a task to be executed by a specific agent.

        Posts to ``/organizations/{org_id}/tasks`` (the agent-tasks endpoint).

        Args:
            agent_id: UUID of the target agent.
            input:    Task input payload (e.g. ``{"message": "Hello, agent!"}``)
            dry_run:  When ``True`` the task is validated but not dispatched.

        Returns:
            Created task dict including ``id`` and ``status``.
        """
        payload: dict[str, Any] = {
            "agentId": agent_id,
            "input": input,
        }
        if dry_run:
            payload["dryRun"] = True
        tasks_url = f"/organizations/{self._http.org_id}/tasks"
        return self._http.post(tasks_url, json=payload)
