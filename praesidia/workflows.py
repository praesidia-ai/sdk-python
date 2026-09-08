"""
Praesidia SDK — WorkflowsResource.

Covers the ``/organizations/{org_id}/workflows`` management endpoints.
"""

from __future__ import annotations

import math
from typing import Any, Iterator

from ._http import HttpClient, path_segment
from ._pagination import normalize_paged_envelope, paginate_all


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

        SCAN2-011 -- returns only the requested page, exactly as before
        (backwards compatible). Use :meth:`list_page` for the full envelope
        or :meth:`list_all` to auto-paginate through every workflow.

        Args:
            page:  1-based page number (default: 1).
            limit: Maximum results per page (default: 20).

        Returns:
            A list of workflow dicts.
        """
        return self.list_page(page=page, limit=limit)["data"]

    def list_page(self, page: int = 1, limit: int = 20) -> dict[str, Any]:
        """Like :meth:`list`, but returns be's full pagination envelope (SCAN2-011)."""
        result = self._http.get(self._base, params={"page": page, "limit": limit})
        return normalize_paged_envelope(result, "workflows")

    def list_all(self, *, limit: int = 20) -> Iterator[dict[str, Any]]:
        """Auto-paginate through every workflow, across every page (SCAN2-011)."""
        yield from paginate_all(lambda page: self.list_page(page=page, limit=limit))

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
            or not math.isfinite(budget_limit_usd)
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

        SCAN2-011 -- returns only the requested page, exactly as before
        (backwards compatible). Use :meth:`list_runs_page` for the full
        envelope or :meth:`list_runs_all` to auto-paginate through every run.

        Args:
            workflow_id: UUID of the workflow.
            page:        1-based page number.
            limit:       Max results per page.

        Returns:
            A list of run dicts.
        """
        return self.list_runs_page(workflow_id, page=page, limit=limit)["data"]

    def list_runs_page(
        self,
        workflow_id: str,
        page: int = 1,
        limit: int = 20,
    ) -> dict[str, Any]:
        """Like :meth:`list_runs`, but returns be's full pagination envelope (SCAN2-011)."""
        result = self._http.get(
            f"{self._base}/{path_segment(workflow_id, 'workflow_id')}/runs",
            params={"page": page, "limit": limit},
        )
        return normalize_paged_envelope(result, "runs")

    def list_runs_all(
        self,
        workflow_id: str,
        *,
        limit: int = 20,
    ) -> Iterator[dict[str, Any]]:
        """Auto-paginate through every run of a workflow, across every page (SCAN2-011)."""
        yield from paginate_all(
            lambda page: self.list_runs_page(workflow_id, page=page, limit=limit)
        )

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
