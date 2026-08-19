"""AUD-0063 — analytics resource coverage gate (Python SDK).

Mirrors ``sdk/src/analytics.coverage.spec.ts``: derives the real
``/organizations/{orgId}/analytics*`` route list from ``ui/swagger.json``
(be-core's exported OpenAPI spec) and fails on any operation
``AnalyticsResource`` does not implement, so a future be-added analytics
route goes RED here instead of silently missing the Python SDK.

The routes under test come from ``_load_analytics_operations()`` below (real
swagger.json paths) -- NOT a second hand-authored route list. ``COVERAGE``
only supplies, per REAL route, which ``AnalyticsResource`` method is supposed
to satisfy it; a route with no entry (or whose entry names a method that does
not exist) fails with a specific, actionable message.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from praesidia import Praesidia

BASE_URL = "http://test.local"
ORG_ID = "org-1"
ANALYTICS_PREFIX = "/organizations/{orgId}/analytics"

_DEFAULT_SPEC_PATH = Path(__file__).resolve().parents[2] / "ui" / "swagger.json"
SPEC_PATH = (
    Path(os.environ["BE_SWAGGER_PATH"])
    if os.environ.get("BE_SWAGGER_PATH")
    else _DEFAULT_SPEC_PATH
)


def _load_analytics_operations() -> list[tuple[str, str]]:
    if not SPEC_PATH.exists():
        raise RuntimeError(
            f"AUD-0063 coverage gate: no swagger.json at {SPEC_PATH} -- export one from "
            "be-core (npm run export:openapi) or set BE_SWAGGER_PATH. Failing closed "
            "rather than skipping."
        )
    spec = json.loads(SPEC_PATH.read_text())
    ops: list[tuple[str, str]] = []
    for path, methods in spec.get("paths", {}).items():
        if path != ANALYTICS_PREFIX and not path.startswith(f"{ANALYTICS_PREFIX}/"):
            continue
        suffix = path[len(ANALYTICS_PREFIX) :] or "/"
        for method in methods:
            if method.lower() in ("get", "post"):
                ops.append((method.upper(), suffix))
    return sorted(ops)


#: Real-swagger-route -> AnalyticsResource method mapping: (method_name, args, kwargs).
COVERAGE: dict[str, tuple[str, tuple[Any, ...], dict[str, Any]]] = {
    "GET /": ("usage", (), {}),
    "GET /capture-state": ("capture_state", (), {}),
    "GET /agents/{agentId}": ("agent_analytics", ("agent-1",), {}),
    "GET /events": ("events", (), {}),
    "POST /events": ("record_event", (), {"event_type": "REQUEST"}),
    "GET /activity-log": ("activity_log", (), {}),
    "GET /advanced/agent-performance": ("agent_performance", (), {}),
    "GET /advanced/security": ("security_metrics", (), {}),
    "GET /advanced/cost-trends": ("cost_trends", (), {}),
    "GET /advanced/usage-heatmap": ("usage_heatmap", (), {}),
    "GET /advanced/top-agents": ("top_agents", (), {}),
    "GET /advanced/compliance": ("compliance_metrics", (), {}),
    "GET /advanced/anomalies": ("anomalies", (), {}),
    "GET /advanced/cost-by-team": ("cost_by_team", (), {}),
    "GET /export": ("export", (), {}),
    "GET /advanced/model-comparison": ("model_comparison", (), {}),
}

OPERATIONS = _load_analytics_operations()


def _client() -> Praesidia:
    return Praesidia(api_key="sk-test", org_id=ORG_ID, base_url=BASE_URL)


def test_be_exports_at_least_the_known_analytics_paths():
    """Sanity check: the real spec loaded and is non-trivial."""
    assert len(OPERATIONS) >= 15


@pytest.mark.parametrize(
    "method,suffix", OPERATIONS, ids=[f"{m}_{s}" for m, s in OPERATIONS]
)
def test_operation_reachable_through_analytics_resource(method: str, suffix: str):
    key = f"{method} {suffix}"
    entry = COVERAGE.get(key)
    if entry is None:
        pytest.fail(
            f"AUD-0063: be exposes {key} (organizations/{{orgId}}/analytics"
            f"{'' if suffix == '/' else suffix}) with no AnalyticsResource coverage entry "
            "and no allow-list reason. Add a resource method (both SDKs) or allow-list it "
            "in COVERAGE with a stated reason."
        )
    method_name, args, kwargs = entry

    client = _client()
    fn = getattr(client.analytics, method_name, None)
    if not callable(fn):
        pytest.fail(
            f"AUD-0063: be exposes {key} but AnalyticsResource has no method "
            f"'{method_name}' yet (the coverage map references it -- add the method to "
            "close the gap)."
        )

    expected_suffix = suffix.replace("{agentId}", "agent-1")
    expected_path = f"/organizations/{ORG_ID}/analytics" + (
        "" if expected_suffix == "/" else expected_suffix
    )
    mock_route = respx.get if method == "GET" else respx.post

    with respx.mock:
        route = mock_route(f"{BASE_URL}{expected_path}").mock(
            return_value=httpx.Response(200, json={})
        )
        fn(*args, **kwargs)
        assert route.called, (
            f"AUD-0063: {method_name}() did not call {method} {expected_path} -- "
            "coverage entry path/method mismatch."
        )
