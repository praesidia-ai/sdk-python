"""Tests for praesidia.audit — stream pagination (BUGHUNT-SDK-01) and the
removed resource_type filter (BUGHUNT-SDK-03)."""

from __future__ import annotations

import httpx
import pytest
import respx

from praesidia import Praesidia

BASE_URL = "https://test.local"
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
    rows = [{"id": f"evt-{i}", "action": "agent.created"} for i in range(total_rows)]

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


@respx.mock
def test_list_sends_all_supported_filters_and_handles_legacy_logs_envelope():
    event = {"id": "evt-1"}
    route = respx.get(AUDIT).mock(
        return_value=httpx.Response(200, json={"logs": [event]})
    )

    result = _client().audit.list(
        from_date="2026-07-01",
        to_date="2026-07-31",
        page=2,
        limit=25,
        action="agent.created",
    )

    assert result == [event]
    assert dict(route.calls.last.request.url.params) == {
        "page": "2",
        "limit": "25",
        "startDate": "2026-07-01",
        "endDate": "2026-07-31",
        "action": "agent.created",
    }


@respx.mock
def test_list_handles_bare_list_response():
    respx.get(AUDIT).mock(return_value=httpx.Response(200, json=[{"id": "evt-1"}]))
    assert _client().audit.list() == [{"id": "evt-1"}]


@respx.mock
def test_stream_sends_from_date_and_handles_legacy_logs_envelope():
    route = respx.get(AUDIT).mock(
        side_effect=[
            httpx.Response(200, json={"logs": [{"id": "evt-1"}]}),
            httpx.Response(200, json={"logs": []}),
        ]
    )

    assert list(_client().audit.stream(from_date="2026-07-01")) == [{"id": "evt-1"}]
    assert route.calls[0].request.url.params["startDate"] == "2026-07-01"


@respx.mock
def test_export_sends_format_and_date_range():
    route = respx.get(f"{AUDIT}/export").mock(
        return_value=httpx.Response(200, content=b"id\n")
    )

    result = _client().audit.export(
        from_date="2026-07-01", to_date="2026-07-31", format="csv"
    )

    assert result == b"id\n"
    assert dict(route.calls.last.request.url.params) == {
        "format": "csv",
        "startDate": "2026-07-01",
        "endDate": "2026-07-31",
    }


def test_export_rejects_unsupported_format_before_network():
    with pytest.raises(ValueError, match="format"):
        _client().audit.export(format="xml")


def test_list_rejects_resource_type_kwarg():
    # Breaking signature change (documented in the README changelog): the
    # parameter was removed because it always 400'd — no working caller
    # could exist. Passing it now raises TypeError at call time.
    with pytest.raises(TypeError):
        _client().audit.list(resource_type="agent")


# --- SDK-0327 — decision receipts + audit packages (BE-1581, BE-1629) -------

AUDIT_API = f"{BASE_URL}/organizations/{ORG_ID}/audit"
PKG_ID = "0b6f7a2e-9c1d-4e3f-8a5b-1c2d3e4f5a6b"
JOB = {"id": PKG_ID, "status": "queued", "error": None, "createdAt": "2026-09-25T00:00:00Z", "completedAt": None}


@respx.mock
def test_receipt_by_decision_id_and_row_id():
    by_decision = respx.get(f"{AUDIT_API}/decisions/dec%2F1/receipt").mock(
        return_value=httpx.Response(200, json={"decisionId": "dec/1"})
    )
    by_row = respx.get(f"{AUDIT_API}/row-1/receipt").mock(return_value=httpx.Response(200, json={"rowId": "row-1"}))
    assert _client().audit.get_decision_receipt("dec/1") == {"decisionId": "dec/1"}
    assert _client().audit.get_receipt("row-1") == {"rowId": "row-1"}
    assert by_decision.called and by_row.called


def test_receipt_rejects_bad_ids_before_network():
    for bad in ("", "..", " x"):
        with pytest.raises(ValueError):
            _client().audit.get_decision_receipt(bad)
        with pytest.raises(ValueError):
            _client().audit.get_receipt(bad)


@respx.mock
def test_request_package_sends_only_given_fields():
    route = respx.post(f"{AUDIT_API}/packages").mock(return_value=httpx.Response(202, json=JOB))
    audit = _client().audit
    assert audit.request_package() == JOB
    assert route.calls[0].request.content == b"{}"
    audit.request_package(from_date="2026-07-01T00:00:00Z", to_date="2026-09-01", ai_system_id=PKG_ID)
    import json as _json

    assert _json.loads(route.calls[1].request.content) == {
        "from": "2026-07-01T00:00:00Z",
        "to": "2026-09-01",
        "aiSystemId": PKG_ID,
    }


def test_request_package_validates_before_network():
    with pytest.raises(ValueError):
        _client().audit.request_package(from_date="yesterday")
    with pytest.raises(ValueError):
        _client().audit.request_package(from_date="2026-09-02", to_date="2026-09-01")
    with pytest.raises(ValueError, match="ai_system_id"):
        _client().audit.request_package(ai_system_id="not-a-uuid")


@respx.mock
def test_get_and_download_package():
    respx.get(f"{AUDIT_API}/packages/{PKG_ID}").mock(return_value=httpx.Response(200, json=JOB))
    respx.get(f"{AUDIT_API}/packages/{PKG_ID}/download").mock(
        return_value=httpx.Response(200, content=b"PK\x03\x04zip", headers={"content-type": "application/zip"})
    )
    assert _client().audit.get_package(PKG_ID) == JOB
    assert _client().audit.download_package(PKG_ID) == b"PK\x03\x04zip"


def test_package_ids_must_be_uuids():
    for call in (_client().audit.get_package, _client().audit.download_package):
        with pytest.raises(ValueError, match="package_id"):
            call("../bundle")


@respx.mock
@pytest.mark.parametrize("status", [409, 410])
def test_download_package_surfaces_not_ready_and_expired(status):
    from praesidia import PraesidiaError

    respx.get(f"{AUDIT_API}/packages/{PKG_ID}/download").mock(return_value=httpx.Response(status))
    with pytest.raises(PraesidiaError) as info:
        _client().audit.download_package(PKG_ID)
    assert info.value.status_code == status


def test_download_package_uses_bounded_stream_get():
    client = _client()
    seen = {}

    def fake(path, params=None, **_):
        seen["path"] = path
        return httpx.Response(200, content=b"zip", request=httpx.Request("GET", BASE_URL + path))

    client.audit._http.stream_get = fake
    assert client.audit.download_package(PKG_ID) == b"zip"
    assert seen["path"].endswith(f"/audit/packages/{PKG_ID}/download")


@respx.mock
def test_export_bundle_include_unrooted_and_window_headers():
    from praesidia import AuditBundle

    route = respx.get(f"{AUDIT_API}/bundle").mock(
        return_value=httpx.Response(
            200,
            content=b"PKzip",
            headers={
                "X-Praesidia-Requested-To": "2026-09-02T00:00:00Z",
                "X-Praesidia-Effective-To": "2026-09-01T23:00:00Z",
                "X-Praesidia-Window-Clamp": "clamped_to_last_rooted_hour",
            },
        )
    )
    audit = _client().audit
    bundle = audit.export_bundle(from_date="2026-09-01", to_date="2026-09-02")
    assert isinstance(bundle, bytes) and isinstance(bundle, AuditBundle) and bundle == b"PKzip"
    assert "includeUnrooted" not in route.calls[0].request.url.params
    assert bundle.requested_to == "2026-09-02T00:00:00Z"
    assert bundle.effective_to == "2026-09-01T23:00:00Z"
    assert bundle.window_clamp == "clamped_to_last_rooted_hour"
    audit.export_bundle(from_date="2026-09-01", to_date="2026-09-02", include_unrooted=True)
    assert route.calls[1].request.url.params["includeUnrooted"] == "true"
