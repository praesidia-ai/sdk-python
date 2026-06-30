"""
Praesidia SDK — ConnectionsResource.

Covers the ``/organizations/{org_id}/connections`` management endpoints.
"""

from __future__ import annotations

from typing import Any

from ._http import HttpClient


class ConnectionsResource:
    """
    Manage external connections (agent integrations, MCP servers, etc.)
    within an organisation.

    Endpoint base: ``/organizations/{org_id}/connections``
    """

    def __init__(self, http: HttpClient) -> None:
        self._http = http
        self._base = f"/organizations/{http.org_id}/connections"

    def list(
        self,
        page: int = 1,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Return a paginated list of connections for the organisation.

        Args:
            page:  1-based page number (default: 1).
            limit: Maximum results per page (default: 20).

        Returns:
            A list of connection dicts.
        """
        result = self._http.get(self._base, params={"page": page, "limit": limit})
        if isinstance(result, list):
            return result
        return result.get("data", result.get("connections", []))

    def get(self, connection_id: str) -> dict[str, Any]:
        """
        Fetch a single connection by ID.

        Args:
            connection_id: UUID of the connection.

        Returns:
            Connection dict.

        Raises:
            NotFoundError: If no connection with that ID exists in the org.
        """
        return self._http.get(f"{self._base}/{connection_id}")

    def create_agent(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a direct-agent connection.

        Posts to ``/organizations/{org_id}/connections/agent``.

        Args:
            data: Connection payload (agentId, name, config, etc.).

        Returns:
            Created connection dict.
        """
        return self._http.post(f"{self._base}/agent", json=data)

    def create_mcp(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Create an MCP server connection.

        Posts to ``/organizations/{org_id}/connections/mcp``.

        Args:
            data: Connection payload (name, url, transport, etc.).

        Returns:
            Created connection dict.
        """
        return self._http.post(f"{self._base}/mcp", json=data)

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a connection using the default agent endpoint.

        Convenience alias for :meth:`create_agent`.

        Args:
            data: Connection payload.

        Returns:
            Created connection dict.
        """
        return self.create_agent(data)

    def update_status(self, connection_id: str, status: str) -> dict[str, Any]:
        """
        Update the active/inactive status of a connection.

        Args:
            connection_id: UUID of the connection.
            status:        New status value (e.g. ``"active"`` or ``"inactive"``).

        Returns:
            Updated connection dict.
        """
        return self._http.patch(
            f"{self._base}/{connection_id}/status",
            json={"status": status},
        )

    def delete(self, connection_id: str) -> None:
        """
        Delete a connection.

        Args:
            connection_id: UUID of the connection to delete.
        """
        self._http.delete(f"{self._base}/{connection_id}")

    def test(self, connection_id: str) -> dict[str, Any]:
        """
        Trigger a connectivity test for a connection.

        Posts to ``/organizations/{org_id}/connections/{id}/test``.

        Args:
            connection_id: UUID of the connection to test.

        Returns:
            Test result dict (latency, success, error, etc.).
        """
        return self._http.post(f"{self._base}/{connection_id}/test")

    def health(self, connection_id: str) -> dict[str, Any]:
        """
        Return the current health status of a connection.

        Args:
            connection_id: UUID of the connection.

        Returns:
            Health dict.
        """
        return self._http.get(f"{self._base}/{connection_id}/health")
