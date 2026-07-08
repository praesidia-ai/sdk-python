"""Tests for praesidia.telemetry — the OTLP/HTTP GenAI trace emitter (H1-02)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from praesidia import Praesidia, gen_ai_span
from praesidia.telemetry import OTLP_MAX_RESOURCE_SPANS, TelemetryResource

BASE_URL = "http://test.local"
ORG_ID = "org-1"
TRACES = f"{BASE_URL}/telemetry/otlp/v1/traces"
ACK = {"accepted": True, "buffered": 1}


def _client() -> Praesidia:
    return Praesidia(api_key="pk-test", org_id=ORG_ID, base_url=BASE_URL)


def _find_attr(attrs, key):
    for a in attrs:
        if a["key"] == key:
            return a["value"]
    return None


@respx.mock
def test_emit_gen_ai_span_posts_otlp_export_request():
    route = respx.post(TRACES).mock(return_value=httpx.Response(202, json=ACK))

    client = _client()
    telemetry = TelemetryResource(client._http, service_name="support-bot")
    ack = telemetry.emit_gen_ai_span(
        agent_name="support-bot",
        system="openai",
        request_model="gpt-4o",
        input_tokens=812,
        output_tokens=143,
    )

    assert ack == ACK
    body = json.loads(route.calls.last.request.content)
    rs = body["resourceSpans"][0]
    assert _find_attr(rs["resource"]["attributes"], "service.name") == {
        "stringValue": "support-bot"
    }
    span = rs["scopeSpans"][0]["spans"][0]
    attrs = span["attributes"]
    assert _find_attr(attrs, "gen_ai.agent.name") == {"stringValue": "support-bot"}
    assert _find_attr(attrs, "gen_ai.system") == {"stringValue": "openai"}
    assert _find_attr(attrs, "gen_ai.request.model") == {"stringValue": "gpt-4o"}
    # OTLP encodes intValue as a string.
    assert _find_attr(attrs, "gen_ai.usage.input_tokens") == {"intValue": "812"}
    assert _find_attr(attrs, "gen_ai.usage.output_tokens") == {"intValue": "143"}
    # AUDIT-SDK-04 — the SDK authenticates with Authorization: Bearer <key>
    # (works on the OTLP JwtOrApiKeyGuard route and everywhere else).
    assert route.calls.last.request.headers["Authorization"] == "Bearer pk-test"


@respx.mock
def test_emit_rejects_oversized_batch_without_calling_network():
    route = respx.post(TRACES)
    too_many = [{"scopeSpans": []} for _ in range(OTLP_MAX_RESOURCE_SPANS + 1)]
    with pytest.raises(ValueError):
        _client().telemetry.emit(too_many)
    assert not route.called


def test_gen_ai_span_builds_client_span_with_hex_ids():
    span = gen_ai_span("bot", request_model="gpt-4o")
    assert span["kind"] == 3
    assert len(span["traceId"]) == 32
    assert len(span["spanId"]) == 16
    assert span["name"] == "chat gpt-4o"
    assert span["startTimeUnixNano"].isdigit()
