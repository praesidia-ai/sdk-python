"""TS parity: page 1..1_000_000 validated, list_lifecycle_requests_page, audit.stream envelope."""

from __future__ import annotations

import httpx
import pytest
import respx

from praesidia import Praesidia

BASE = "https://test.local"
ORG = f"{BASE}/organizations/org-1"


def _c() -> Praesidia:
    return Praesidia(api_key="sk-test", org_id="org-1", base_url=BASE)


@respx.mock
def test_lifecycle_requests_page_keeps_meta():
    meta = {"page": 1, "limit": 1, "total": 3, "totalPages": 3, "hasNextPage": True}
    respx.get(f"{ORG}/ai-systems/lifecycle-requests").mock(
        return_value=httpx.Response(200, json={"data": [{"id": "r1"}], "total": 3, "meta": meta})
    )
    out = _c().ai_systems.list_lifecycle_requests_page(limit=1)
    assert out["meta"]["hasNextPage"] is True and out["total"] == 3
    with pytest.raises(ValueError, match="limit"):
        _c().ai_systems.list_lifecycle_requests_page(limit=101)


@respx.mock
@pytest.mark.parametrize("bad", [0, -1, 1_000_001, True, 1.5])
def test_page_bounds_rejected_before_request(bad):
    c = _c()
    calls = [
        lambda: c.ai_systems.list_lifecycle_requests_page(page=bad),
        lambda: c.agents.list_page(page=bad),
        lambda: c.audit.list_page(page=bad),
        lambda: c.analytics.events_page("a", page=bad),
        lambda: c.memory.list(page=bad),
    ]
    for call in calls:
        with pytest.raises(ValueError, match="page"):
            call()


@respx.mock
def test_audit_stream_unwraps_logs_shape():
    route = respx.get(f"{ORG}/audit-logs").mock(
        side_effect=[
            httpx.Response(200, json={"logs": [{"id": 1}]}),
            httpx.Response(200, json={"data": []}),
        ]
    )
    assert list(_c().audit.stream()) == [{"id": 1}]
    assert route.call_count == 2
