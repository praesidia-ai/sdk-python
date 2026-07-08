"""Tests for praesidia.audit — stream pagination (BUGHUNT-SDK-01) and the
removed resource_type filter (BUGHUNT-SDK-03)."""

from __future__ import annotations

import httpx
import pytest
import respx

from praesidia import Praesidia

BASE_URL = "http://test.local"
ORG_ID = "org-1"
AUDIT = f"{BASE_URL}/organizations/{ORG_ID}/audit-logs"

# Mirrors the backend PAGINATION_MAX_LIMIT / clampLimit hard cap.
SERVER_PAGE_CAP = 100


def _client() -> Praesidia:
    return Praesidia(api_key="sk-test", org_id=ORG_ID, base_url=BASE_URL)


def _paged_server(total_rows: int):
    """respx side_effect mimicking the backend's clamped pagination: at most
    SERVER_PAGE_CAP rows for the requested page, empty once the offset runs
    past the data — exactly the shape that exposed BUGHUNT-SDK-01."""
    rows = [
        {"id": f"evt-{i}", "action": "agent.created"} for i in range(total_rows)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        page = int(params.get("page", "1"))
        requested = int(params.get("limit", "50"))
        page_size = min(requested, SERVER_PAGE_CAP)
        start = (page - 1) * page_size
        chunk = rows[start : start + page_size]
        return httpx.Response(200, json={"data": chunk, "total": total_rows})

    return handler


# --- BUGHUNT-SDK-01 — stream paginates past the server page cap ------------


@respx.mock
def test_stream_paginates_full_range_when_limit_over_server_cap():
    # 250 rows, caller asks for a 500-row page. The server clamps every page
    # to 100, so page 1 returns 100 < 500 — the OLD `len(events) < limit`
    # heuristic stopped right there and silently dropped 150 rows.
    total = 250
    respx.get(AUDIT).mock(side_effect=_paged_server(total))

    events = list(_client().audit.stream(limit=500))

    assert len(events) == total
    assert [e["id"] for e in events] == [f"evt-{i}" for i in range(total)]
    # No duplicates, no gaps.
    assert len({e["id"] for e in events}) == total


@respx.mock
def test_stream_stops_on_empty_page_at_exact_cap_multiple():
    # Exactly 200 rows at the 100 cap → page1=100, page2=100, page3=empty.
    total = 200
    route = respx.get(AUDIT).mock(side_effect=_paged_server(total))

    events = list(_client().audit.stream(limit=100))

    assert len(events) == total
    # Two full pages + one empty terminator page.
    assert route.call_count == 3


@respx.mock
def test_stream_default_limit_streams_all():
    total = 150
    respx.get(AUDIT).mock(side_effect=_paged_server(total))

    events = list(_client().audit.stream())  # default limit=100

    assert len(events) == total


@respx.mock
def test_stream_handles_bare_list_response_shape():
    # Some deployments return a bare list rather than {"data": [...]}.
    rows = [{"id": f"evt-{i}"} for i in range(100)]

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, json=rows if page == 1 else [])

    respx.get(AUDIT).mock(side_effect=handler)

    events = list(_client().audit.stream(limit=100))
    assert len(events) == 100


# --- BUGHUNT-SDK-03 — resource_type filter removed ------------------------


@respx.mock
def test_list_does_not_send_resource_type_param():
    route = respx.get(AUDIT).mock(
        return_value=httpx.Response(200, json={"data": [], "total": 0})
    )
    _client().audit.list(action="agent.created")

    params = route.calls.last.request.url.params
    assert params["action"] == "agent.created"
    # resourceType is not a backend filter — sending it under
    # forbidNonWhitelisted 400'd the WHOLE request. It must never be sent.
    assert "resourceType" not in params


def test_list_rejects_resource_type_kwarg():
    # Breaking signature change (documented in the README changelog): the
    # parameter was removed because it always 400'd — no working caller
    # could exist. Passing it now raises TypeError at call time.
    with pytest.raises(TypeError):
        _client().audit.list(resource_type="agent")
