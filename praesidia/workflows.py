"""
Praesidia SDK — WorkflowsResource.

Covers the ``/organizations/{org_id}/workflows`` management endpoints.
"""

from __future__ import annotations

from typing import Any

from ._http import HttpClient, path_segment


class WorkflowsResource:
    """
    Manage approval workflows within an organisation.

    Endpoint base: ``/organizations/{org_id}/workflows``
    """

    def __init__(self, http: HttpClient) -> None:
        self._http = http
        self._base = f"/organizations/{http.org_id}/workflows"

    def list(
        self,
        page: int = 1,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Return a paginated list of workflows.

        Args:
            page:  1-based page number (default: 1).
            limit: Maximum results per page (default: 20).

        Returns:
            A list of workflow dicts.
        """
        result = self._http.get(self._base, params={"page": page, "limit": limit})
        if isinstance(result, list):
            return result
        return result.get("data", result.get("workflows", []))

    def get(self, workflow_id: str) -> dict[str, Any]:
        """
        Fetch a single workflow by ID.

        Args:
            workflow_id: UUID of the workflow.

        Returns:
            Workflow dict.

        Raises:
            NotFoundError: If the workflow does not exist in the org.
        """
        return self._http.get(
            f"{self._base}/{path_segment(workflow_id, 'workflow_id')}"
        )

    def create(self, data: dict[str, Any]) -> dict[str, Any]:
        """
        Create a new workflow.

        Args:
            data: Workflow creation payload (name, nodes, edges, etc.).

        Returns:
            Created workflow dict.
        """
        return self._http.post(self._base, json=data)

    def update(self, workflow_id: str, data: dict[str, Any]) -> dict[str, Any]:
        """
        Partially update a workflow.

        Args:
            workflow_id: UUID of the workflow to update.
            data:        Fields to update.

        Returns:
            Updated workflow dict.
        """
        return self._http.patch(
            f"{self._base}/{path_segment(workflow_id, 'workflow_id')}", json=data
        )

    def delete(self, workflow_id: str) -> None:
        """
        Delete a workflow.

        Args:
            workflow_id: UUID of the workflow to delete.
        """
        self._http.delete(
            f"{self._base}/{path_segment(workflow_id, 'workflow_id')}"
        )

    def trigger(
        self,
        workflow_id: str,
        input: dict[str, Any] | None = None,
        *,
        budget_limit_usd: float | None = None,
    ) -> dict[str, Any]:
        """
        Start a new run for a workflow.

        Posts to ``/organizations/{org_id}/workflows/{id}/runs``.

        Args:
            workflow_id: UUID of the workflow to trigger.
            input:       Optional run-level input payload.
            budget_limit_usd: Optional non-negative auto-pause threshold.

        Returns:
            Created run dict including ``id`` and ``status``.
        """
        if input is not None and not isinstance(input, dict):
            raise ValueError("input must be a dict or None")
        if budget_limit_usd is not None and (
            isinstance(budget_limit_usd, bool)
            or not isinstance(budget_limit_usd, (int, float))
            or budget_limit_usd < 0
        ):
            raise ValueError("budget_limit_usd must be a non-negative number")
        body: dict[str, Any] = {"initialInput": input or {}}
        if budget_limit_usd is not None:
            body["budgetLimitUsd"] = budget_limit_usd
        return self._http.post(
            f"{self._base}/{path_segment(workflow_id, 'workflow_id')}/runs",
            json=body,
        )

    def list_runs(
        self,
        workflow_id: str,
        page: int = 1,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        List execution runs for a workflow.

        Args:
            workflow_id: UUID of the workflow.
            page:        1-based page number.
            limit:       Max results per page.

        Returns:
            A list of run dicts.
        """
        result = self._http.get(
            f"{self._base}/{path_segment(workflow_id, 'workflow_id')}/runs",
            params={"page": page, "limit": limit},
        )
        if isinstance(result, list):
            return result
        return result.get("data", result.get("runs", []))

    def get_run(self, workflow_id: str, run_id: str) -> dict[str, Any]:
        """
        Fetch a specific workflow run.

        Args:
            workflow_id: UUID of the workflow.
            run_id:      UUID of the run.

        Returns:
            Run dict.
        """
        return self._http.get(
            f"{self._base}/{path_segment(workflow_id, 'workflow_id')}"
            f"/runs/{path_segment(run_id, 'run_id')}"
        )
