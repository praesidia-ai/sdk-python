"""Tests for praesidia._retry and the HttpClient retry wiring (FINDING-4).

Bounded retry is applied ONLY to GET/DELETE (always idempotent in this API)
and to POST/PATCH when the caller supplies an idempotency_key. A bare POST
must never be retried -- that would risk a duplicate create/charge.
"""

from __future__ import annotations

import httpx
import pytest

from praesidia._http import HttpClient
from praesidia._retry import (
    DEFAULT_RETRY_CONFIG,
    RetryConfig,
    assert_idempotency_key_supported,
    compute_backoff_s,
    is_retryable_status,
    parse_retry_after_s,
    resolve_retry_config,
)

# Small, fast policy so specs run instantly and deterministically.
FAST_RETRY = RetryConfig(max_attempts=3, base_delay_s=0.001, max_delay_s=0.002, max_elapsed_s=5.0)


# ---------------------------------------------------------------------------
# resolve_retry_config
# ---------------------------------------------------------------------------


def test_resolve_retry_config_false_disables():
    assert resolve_retry_config(False) is None


def test_resolve_retry_config_none_is_default():
    assert resolve_retry_config(None) == DEFAULT_RETRY_CONFIG


def test_resolve_retry_config_accepts_custom():
    cfg = RetryConfig(max_attempts=5)
    assert resolve_retry_config(cfg) == cfg


@pytest.mark.parametrize(
    "cfg",
    [
        RetryConfig(max_attempts=0),
        RetryConfig(max_attempts=11),
        RetryConfig(max_attempts=1.5),  # type: ignore[arg-type]
        RetryConfig(base_delay_s=-1),
        RetryConfig(base_delay_s=float("nan")),
        RetryConfig(base_delay_s=float("inf")),
        RetryConfig(base_delay_s=500, max_delay_s=100),
        RetryConfig(max_delay_s=float("nan")),
        RetryConfig(max_delay_s=float("inf")),
        RetryConfig(max_elapsed_s=-1),
        RetryConfig(max_elapsed_s=float("nan")),
        RetryConfig(max_elapsed_s=float("inf")),
    ],
)
def test_resolve_retry_config_rejects_invalid(cfg):
    with pytest.raises(ValueError, match="retry"):
        resolve_retry_config(cfg)


def test_resolve_retry_config_rejects_non_retryconfig():
    with pytest.raises(ValueError, match="retry"):
        resolve_retry_config("not-a-config")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# is_retryable_status / parse_retry_after_s / compute_backoff_s
# ---------------------------------------------------------------------------


def test_is_retryable_status():
    assert is_retryable_status(429)
    assert is_retryable_status(500)
    assert is_retryable_status(503)
    assert is_retryable_status(599)
    assert not is_retryable_status(200)
    assert not is_retryable_status(400)
    assert not is_retryable_status(404)
    assert not is_retryable_status(409)


def test_parse_retry_after_s_delta_seconds():
    assert parse_retry_after_s("2") == 2.0
    assert parse_retry_after_s("0") == 0.0


def test_parse_retry_after_s_http_date():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=5))
    delay = parse_retry_after_s(future)
    assert delay is not None
    assert 0 < delay <= 5


def test_parse_retry_after_s_invalid_returns_none():
    assert parse_retry_after_s(None) is None
    assert parse_retry_after_s("not-a-date-or-number") is None
    assert parse_retry_after_s("nan") is None
    assert parse_retry_after_s("inf") is None


def test_compute_backoff_s_bounded():
    for attempt in range(1, 7):
        delay = compute_backoff_s(attempt, 0.1, 1.0)
        assert 0 <= delay <= 1.0
    assert compute_backoff_s(20, 0.1, 0.5) <= 0.5


# ---------------------------------------------------------------------------
# HttpClient integration
# ---------------------------------------------------------------------------


def test_get_retries_on_503_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, request=httpx.Request("GET", url))
        return httpx.Response(200, json={"ok": True}, request=httpx.Request("GET", url))

    monkeypatch.setattr("praesidia._http.httpx.get", fake_get)
    client = HttpClient(api_key="k", org_id="o", base_url="http://test.local", retry=FAST_RETRY)
    assert client.get("/health") == {"ok": True}
    assert calls["n"] == 2


def test_get_honours_retry_after_on_429(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                429, headers={"Retry-After": "0"}, request=httpx.Request("GET", url)
            )
        return httpx.Response(200, json={"ok": True}, request=httpx.Request("GET", url))

    monkeypatch.setattr("praesidia._http.httpx.get", fake_get)
    client = HttpClient(api_key="k", org_id="o", base_url="http://test.local", retry=FAST_RETRY)
    assert client.get("/health") == {"ok": True}
    assert calls["n"] == 2


def test_delete_retries_on_transient_500(monkeypatch):
    calls = {"n": 0}

    def fake_delete(url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(500, request=httpx.Request("DELETE", url))
        return httpx.Response(204, request=httpx.Request("DELETE", url))

    monkeypatch.setattr("praesidia._http.httpx.delete", fake_delete)
    client = HttpClient(api_key="k", org_id="o", base_url="http://test.local", retry=FAST_RETRY)
    client.delete("/resource/1")
    assert calls["n"] == 2


def test_bare_post_is_never_retried(monkeypatch):
    calls = {"n": 0}

    def fake_post(url, **kwargs):
        calls["n"] += 1
        return httpx.Response(503, request=httpx.Request("POST", url))

    monkeypatch.setattr("praesidia._http.httpx.post", fake_post)
    client = HttpClient(api_key="k", org_id="o", base_url="http://test.local", retry=FAST_RETRY)
    with pytest.raises(Exception):
        client.post("/tasks", json={"input": {}})
    assert calls["n"] == 1


def test_post_with_idempotency_key_is_retried_and_sends_header(monkeypatch):
    calls = {"n": 0}
    seen_headers = []

    def fake_post(url, **kwargs):
        calls["n"] += 1
        seen_headers.append(dict(kwargs.get("headers") or {}))
        if calls["n"] == 1:
            return httpx.Response(503, request=httpx.Request("POST", url))
        return httpx.Response(
            201, json={"created": True}, request=httpx.Request("POST", url)
        )

    monkeypatch.setattr("praesidia._http.httpx.post", fake_post)
    client = HttpClient(api_key="k", org_id="o", base_url="http://test.local", retry=FAST_RETRY)
    result = client.post(
        "/organizations/o/tasks", json={"input": {}}, idempotency_key="idem-123"
    )
    assert result == {"created": True}
    assert calls["n"] == 2
    assert seen_headers[0]["Idempotency-Key"] == "idem-123"


def test_idempotency_key_rejected_on_a_route_be_core_does_not_dedup(monkeypatch):
    """R-SDK-1 -- be-core ignores Idempotency-Key everywhere except the
    allow-listed task routes; passing one to any other path must fail closed
    (no request sent) rather than silently retry an un-deduplicated write."""
    calls = {"n": 0}

    def fake_post(url, **kwargs):
        calls["n"] += 1
        return httpx.Response(201, json={"id": "x"}, request=httpx.Request("POST", url))

    monkeypatch.setattr("praesidia._http.httpx.post", fake_post)
    client = HttpClient(api_key="k", org_id="o", base_url="http://test.local", retry=FAST_RETRY)
    with pytest.raises(ValueError, match="does not honour Idempotency-Key"):
        client.post("/organizations/o/agents", json={"name": "a"}, idempotency_key="idem-123")
    assert calls["n"] == 0


def test_idempotency_key_rejected_on_every_patch_route(monkeypatch):
    """R-SDK-1 -- be-core honours Idempotency-Key on no PATCH route today."""
    client = HttpClient(api_key="k", org_id="o", base_url="http://test.local", retry=FAST_RETRY)
    with pytest.raises(ValueError, match="does not honour Idempotency-Key"):
        client.patch(
            "/organizations/o/tasks", json={"name": "a"}, idempotency_key="idem-123"
        )


def test_idempotency_key_allowed_on_a2a_inbound_routes(monkeypatch):
    def fake_post(url, **kwargs):
        return httpx.Response(201, json={"ok": True}, request=httpx.Request("POST", url))

    monkeypatch.setattr("praesidia._http.httpx.post", fake_post)
    client = HttpClient(api_key="k", org_id="o", base_url="http://test.local", retry=FAST_RETRY)
    assert client.post("/a2a/tasks", json={}, idempotency_key="k") == {"ok": True}
    assert client.post(
        "/a2a/tasks/task-1/result", json={}, idempotency_key="k"
    ) == {"ok": True}


def test_gives_up_after_max_attempts(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, **kwargs):
        calls["n"] += 1
        return httpx.Response(503, request=httpx.Request("GET", url))

    monkeypatch.setattr("praesidia._http.httpx.get", fake_get)
    client = HttpClient(api_key="k", org_id="o", base_url="http://test.local", retry=FAST_RETRY)
    with pytest.raises(Exception):
        client.get("/health")
    assert calls["n"] == FAST_RETRY.max_attempts


def test_retry_false_disables_retries(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, **kwargs):
        calls["n"] += 1
        return httpx.Response(503, request=httpx.Request("GET", url))

    monkeypatch.setattr("praesidia._http.httpx.get", fake_get)
    client = HttpClient(api_key="k", org_id="o", base_url="http://test.local", retry=False)
    with pytest.raises(Exception):
        client.get("/health")
    assert calls["n"] == 1


def test_network_level_failure_is_retried(monkeypatch):
    calls = {"n": 0}

    def fake_get(url, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection reset", request=httpx.Request("GET", url))
        return httpx.Response(200, json={"ok": True}, request=httpx.Request("GET", url))

    monkeypatch.setattr("praesidia._http.httpx.get", fake_get)
    client = HttpClient(api_key="k", org_id="o", base_url="http://test.local", retry=FAST_RETRY)
    assert client.get("/health") == {"ok": True}
    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# assert_idempotency_key_supported (R-SDK-1) -- the idempotency_key
# allow-list. be-core honours Idempotency-Key on exactly three routes;
# everything else must be rejected client-side. Parity with the TS SDK's
# retry.spec.ts.
# ---------------------------------------------------------------------------


def test_assert_idempotency_key_supported_allows_task_creation():
    assert_idempotency_key_supported("POST", "/organizations/org_1/tasks")


def test_assert_idempotency_key_supported_allows_a2a_inbound_routes():
    assert_idempotency_key_supported("POST", "/a2a/tasks")
    assert_idempotency_key_supported("POST", "/a2a/tasks/task-1/result")


def test_assert_idempotency_key_supported_rejects_other_post_paths():
    with pytest.raises(ValueError, match="does not honour Idempotency-Key"):
        assert_idempotency_key_supported("POST", "/organizations/org_1/agents")
    with pytest.raises(ValueError, match="does not honour Idempotency-Key"):
        assert_idempotency_key_supported(
            "POST", "/organizations/org_1/tasks/task-1/approve"
        )


def test_assert_idempotency_key_supported_rejects_every_patch_path():
    with pytest.raises(ValueError, match="does not honour Idempotency-Key"):
        assert_idempotency_key_supported("PATCH", "/organizations/org_1/tasks")
