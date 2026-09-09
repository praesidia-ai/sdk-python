"""Tests for praesidia.telemetry — the OTLP/HTTP GenAI trace emitter (H1-02)."""

from __future__ import annotations

import json
from importlib.metadata import version

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
    span = gen_ai_span(
        "bot",
        agent_id="agent-1",
        system="openai",
        request_model="gpt-4o",
        response_model="gpt-4o-2026-08-01",
        operation_name="chat",
        input_tokens=10,
        output_tokens=5,
        name="named-span",
        duration_ms=25,
        extra_attributes=[{"key": "custom", "value": {"stringValue": "value"}}],
    )
    assert span["kind"] == 3
    assert len(span["traceId"]) == 32
    assert len(span["spanId"]) == 16
    assert span["name"] == "named-span"
    assert span["startTimeUnixNano"].isdigit()
    attrs = span["attributes"]
    assert _find_attr(attrs, "gen_ai.agent.id") == {"stringValue": "agent-1"}
    assert _find_attr(attrs, "gen_ai.response.model") == {
        "stringValue": "gpt-4o-2026-08-01"
    }
    assert _find_attr(attrs, "gen_ai.operation.name") == {"stringValue": "chat"}
    assert _find_attr(attrs, "custom") == {"stringValue": "value"}
    assert int(span["endTimeUnixNano"]) - int(span["startTimeUnixNano"]) == 25_000_000


@pytest.mark.parametrize(
    ("agent_name", "kwargs", "message"),
    [
        ("", {}, "agent_name"),
        (" bot", {}, "agent_name"),
        ("bot", {"input_tokens": -1}, "input_tokens"),
        ("bot", {"input_tokens": True}, "input_tokens"),
        ("bot", {"output_tokens": 1.5}, "output_tokens"),
        ("bot", {"duration_ms": -1}, "duration_ms"),
        ("bot", {"request_model": " model"}, "request_model"),
        ("bot", {"extra_attributes": {}}, "extra_attributes"),
        ("bot", {"extra_attributes": ["invalid"]}, "extra_attributes"),
    ],
)
def test_gen_ai_span_rejects_values_that_would_be_dropped_or_corrupted(
    agent_name, kwargs, message
):
    with pytest.raises(ValueError, match=message):
        gen_ai_span(agent_name, **kwargs)


def test_emit_gen_ai_spans_requires_at_least_one_span():
    with pytest.raises(ValueError, match="non-empty"):
        _client().telemetry.emit_gen_ai_spans([])


@pytest.mark.parametrize("resource_spans", [None, ["invalid"]])
def test_emit_rejects_invalid_raw_resource_spans(resource_spans):
    with pytest.raises(ValueError, match="resourceSpans"):
        _client().telemetry.emit(resource_spans)


def test_emit_rejects_body_over_server_limit():
    oversized = [{"scopeSpans": [], "padding": "x" * (5 * 1024 * 1024)}]
    with pytest.raises(ValueError, match="body limit"):
        _client().telemetry.emit(oversized)


def test_build_resource_spans_without_service_name_omits_resource_identity():
    span = gen_ai_span("bot")
    result = _client().telemetry.build_gen_ai_resource_spans([span])
    assert "resource" not in result[0]


def test_otlp_scope_uses_published_sdk_version():
    span = gen_ai_span("bot")
    result = _client().telemetry.build_gen_ai_resource_spans([span])
    assert result[0]["scopeSpans"][0]["scope"]["version"] == version("praesidia")


def test_telemetry_service_name_matches_backend_identity_bounds():
    with pytest.raises(ValueError, match="service_name"):
        TelemetryResource(_client()._http, service_name=" ")
