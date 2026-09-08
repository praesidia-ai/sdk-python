"""SCAN2-007 — be returns a structured JSON error envelope
({statusCode, timestamp, path, method, requestId, message, details?,
retryAfter?, code?, ...extra}, see
be/src/common/filters/http-exception.filter.ts). Today the typed exception
hierarchy in praesidia.exceptions discards that body -- only ``r.text`` (the
raw string) reaches ``message``. These tests drive the real HTTP boundary
(respx stubbing the actual wire response, so the real
``HttpClient._raise_for_status`` construction path runs) and assert the
raised exception exposes the envelope's fields as typed attributes.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from praesidia import Praesidia
from praesidia.exceptions import ForbiddenError, PraesidiaError, RateLimitError

BASE_URL = "http://test.local"
ORG_ID = "org-1"
AGENTS_URL = f"{BASE_URL}/organizations/{ORG_ID}/agents"


def _client() -> Praesidia:
    return Praesidia(api_key="sk-test", org_id=ORG_ID, base_url=BASE_URL)


@respx.mock
def test_rate_limit_error_exposes_code_request_id_details_retry_after_and_retryable():
    envelope = {
        "statusCode": 429,
        "timestamp": "2026-09-07T00:00:00.000Z",
        "path": f"/organizations/{ORG_ID}/agents",
        "method": "GET",
        "requestId": "req-abc123",
        "message": "Too many requests. Please try again later.",
        "code": "RATE_LIMITED",
        "details": {"limit": 100, "windowSeconds": 60},
        "retryAfter": 30,
    }
    respx.get(AGENTS_URL).mock(
        return_value=httpx.Response(429, json=envelope)
    )

    client = _client()
    with pytest.raises(RateLimitError) as exc_info:
        client.agents.list()

    err = exc_info.value
    assert isinstance(err, PraesidiaError)
    assert err.status_code == 429
    assert err.code == "RATE_LIMITED"
    assert err.request_id == "req-abc123"
    assert err.details == {"limit": 100, "windowSeconds": 60}
    assert err.retry_after == 30
    assert err.retryable is True
    # Forward compatibility: the full raw envelope stays available so a
    # field be adds later is not silently dropped by an older SDK.
    assert err.body == envelope
    # Backwards compatibility: existing callers reading .message still work.
    assert isinstance(err.message, str)
    assert len(err.message) > 0


@respx.mock
def test_forbidden_error_is_not_retryable_and_has_no_retry_after():
    envelope = {
        "statusCode": 403,
        "timestamp": "2026-09-07T00:00:00.000Z",
        "path": f"/organizations/{ORG_ID}/agents",
        "method": "GET",
        "requestId": "req-def456",
        "message": "Forbidden",
        "code": "FORBIDDEN",
    }
    respx.get(AGENTS_URL).mock(return_value=httpx.Response(403, json=envelope))

    client = _client()
    with pytest.raises(ForbiddenError) as exc_info:
        client.agents.list()

    err = exc_info.value
    assert err.code == "FORBIDDEN"
    assert err.retry_after is None
    assert err.retryable is False
    assert err.details is None


@respx.mock
def test_degrades_gracefully_on_non_json_error_body():
    respx.get(AGENTS_URL).mock(
        return_value=httpx.Response(502, text="upstream gateway is down")
    )

    client = _client()
    with pytest.raises(PraesidiaError) as exc_info:
        client.agents.list()

    err = exc_info.value
    assert err.status_code == 502
    assert err.code is None
    assert err.request_id is None
    assert err.details is None
    assert err.body is None
    assert err.retryable is True  # 5xx is still retryable even when unparsed
    assert "upstream gateway is down" in err.message


@respx.mock
def test_api_key_never_reaches_message_repr_or_str():
    secret_key = "sk_live_SUPER_SECRET_DO_NOT_LEAK"
    envelope = {
        "statusCode": 403,
        "timestamp": "2026-09-07T00:00:00.000Z",
        "path": f"/organizations/{ORG_ID}/agents",
        "method": "GET",
        "requestId": "req-sec789",
        "message": "Forbidden",
        "code": "FORBIDDEN",
    }
    respx.get(AGENTS_URL).mock(return_value=httpx.Response(403, json=envelope))

    client = Praesidia(api_key=secret_key, org_id=ORG_ID, base_url=BASE_URL)
    with pytest.raises(ForbiddenError) as exc_info:
        client.agents.list()

    err = exc_info.value
    assert secret_key not in err.message
    assert secret_key not in str(err)
    assert secret_key not in repr(err)
    assert secret_key not in json.dumps(err.body)
