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
