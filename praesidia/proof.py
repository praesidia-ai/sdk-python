"""Protected-action evidence reads. Retrieval never establishes verification."""
from __future__ import annotations

from typing import Any

from ._evidence import evidence_date_range
from ._http import HttpClient, path_segment

PROTECTED_ACTION_CLOSURES = (
    "DENIED", "EXPIRED", "CANCELLED_BEFORE_DISPATCH", "TARGET_REJECTED",
    "SUCCEEDED", "FAILED_NO_EFFECT", "PARTIAL", "REVERSED",
    "DUPLICATE_SUPPRESSED", "OUTCOME_UNKNOWN", "EVIDENCE_INCOMPLETE",
)


class ProofResource:
    """Requires a personal user-backed key with audit:read, proof.actions,
    and the user's protected_actions.view permission. Workload keys do not qualify.
    """

    def __init__(self, http: HttpClient) -> None:
        self._http = http
        self._base = f"/organizations/{http.org_id}/protected-actions"

    def list(
        self, *, agent_id: str | None = None, task_id: str | None = None,
        chain_id: str | None = None, state: str | None = None,
        closure: str | None = None, from_date: str | None = None,
        to_date: str | None = None, page: int = 1, limit: int = 100,
    ) -> dict[str, Any]:
        """Return data/total/meta unchanged; from is inclusive and to exclusive."""
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ValueError("page must be a positive integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        evidence_date_range(from_date, to_date)
        if closure is not None and closure not in PROTECTED_ACTION_CLOSURES:
            raise ValueError("closure must be a supported protected-action closure")
        params: dict[str, Any] = {"page": page, "limit": limit}
        for key, value in (("agentId", agent_id), ("taskId", task_id), ("chainId", chain_id), ("state", state), ("closure", closure), ("from", from_date), ("to", to_date)):
            if value is not None:
                path_segment(value, key)
                params[key] = value
        return self._http.get(self._base, params=params)

    def get(self, action_id: str) -> dict[str, Any]:
        """Read a projection. Operational success does not imply valid evidence."""
        return self._http.get(f"{self._base}/{path_segment(action_id, 'action_id')}")

    def events(self, action_id: str) -> list[dict[str, Any]]:
        """Return signed events unchanged, including decimal actionSeq and null payloads."""
        return self._http.get(f"{self._base}/{path_segment(action_id, 'action_id')}/events")

    def capture_scope(self) -> list[dict[str, Any]]:
        """Read the declared denominator, including partial and unsupported edges."""
        return self._http.get(f"{self._base}/capture-scope")

    def coverage_summary(self) -> dict[str, Any]:
        """Return exact server totals; do not estimate coverage from a sampled page."""
        return self._http.get(f"{self._base}/coverage-summary")
