"""
Praesidia SDK — TelemetryResource (H1-02): OTLP/HTTP GenAI trace EMITTER.

This is how an SDK-instrumented agent becomes an **OBSERVED** agent: it pushes
OTLP/HTTP GenAI-convention traces to ``POST /telemetry/otlp/v1/traces``, which
buffers them and materialises the emitting agent from the GenAI spans (with zero
backend registration).

It is a MINIMAL emitter — it does NOT vendor the OpenTelemetry SDK (the Python
SDK's only runtime dependency is ``httpx``). If you already run the OpenTelemetry
SDK, point its OTLP/HTTP exporter at the endpoint with an
``Authorization: Bearer <org pk_ key>`` header instead; this class is the
dependency-light path for agents that don't.

Auth: an ORGANIZATION API key. The endpoint takes the tenant SOLELY from the key
(there is no org id in the path) and accepts it as either ``Authorization:
Bearer`` or ``X-API-Key``. AUDIT-SDK-04 — the Python client sends
``Authorization: Bearer <key>`` like every other resource (matching the TS SDK /
CLI and the backend's canonical ``ApiKeyStrategy``), so it authenticates on this
route and on every ``OrAuthGuard`` route too.
"""

from __future__ import annotations

import os
import time
from typing import Any, Optional

from ._http import HttpClient

#: Top-level OTLP/HTTP GenAI trace ingest endpoint.
OTLP_TRACES_PATH = "/telemetry/otlp/v1/traces"

#: Server-side cap on resourceSpans[] per request (mirrors OTLP_LIMITS).
OTLP_MAX_RESOURCE_SPANS = 100
#: Server-side raw body cap in bytes (global 2 MB limit).
OTLP_MAX_BODY_BYTES = 2 * 1024 * 1024

#: OpenTelemetry GenAI semantic-convention attribute keys the be-core receiver
#: reads (kept in lock-step so a span materialises into an OBSERVED agent).
_GENAI = {
    "system": "gen_ai.system",
    "request_model": "gen_ai.request.model",
    "response_model": "gen_ai.response.model",
    "agent_name": "gen_ai.agent.name",
    "agent_id": "gen_ai.agent.id",
    "operation_name": "gen_ai.operation.name",
    "input_tokens": "gen_ai.usage.input_tokens",
    "output_tokens": "gen_ai.usage.output_tokens",
}
_SERVICE_NAME_ATTR = "service.name"
_SPAN_KIND_CLIENT = 3  # SPAN_KIND_CLIENT — a GenAI inference call is a client span
_SDK_SCOPE_VERSION = "0.1.0"


def _str_attr(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


def _int_attr(key: str, value: int) -> dict[str, Any]:
    # OTLP encodes intValue as a string.
    return {"key": key, "value": {"intValue": str(value)}}


def gen_ai_span(
    agent_name: str,
    *,
    agent_id: Optional[str] = None,
    system: Optional[str] = None,
    request_model: Optional[str] = None,
    response_model: Optional[str] = None,
    operation_name: Optional[str] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    name: Optional[str] = None,
    duration_ms: int = 0,
    extra_attributes: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """
    Synthesize ONE OTLP GenAI-convention span from simple inputs. Attribute keys
    mirror the backend GenAI parser exactly so the emitting agent is materialised
    as an OBSERVED agent.
    """
    attributes: list[dict[str, Any]] = [_str_attr(_GENAI["agent_name"], agent_name)]
    if agent_id is not None:
        attributes.append(_str_attr(_GENAI["agent_id"], agent_id))
    if system is not None:
        attributes.append(_str_attr(_GENAI["system"], system))
    if request_model is not None:
        attributes.append(_str_attr(_GENAI["request_model"], request_model))
    if response_model is not None:
        attributes.append(_str_attr(_GENAI["response_model"], response_model))
    if operation_name is not None:
        attributes.append(_str_attr(_GENAI["operation_name"], operation_name))
    if input_tokens is not None:
        attributes.append(_int_attr(_GENAI["input_tokens"], input_tokens))
    if output_tokens is not None:
        attributes.append(_int_attr(_GENAI["output_tokens"], output_tokens))
    if extra_attributes:
        attributes.extend(extra_attributes)

    start_ns = time.time_ns()
    span_name = name or f"{operation_name or 'chat'} {request_model or ''}".strip()
    return {
        "traceId": os.urandom(16).hex(),
        "spanId": os.urandom(8).hex(),
        "name": span_name,
        "kind": _SPAN_KIND_CLIENT,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(start_ns + duration_ms * 1_000_000),
        "attributes": attributes,
    }


class TelemetryResource:
    """
    Emit OTLP/HTTP GenAI traces so an agent shows up as an OBSERVED agent.

    Example::

        client.telemetry.emit_gen_ai_span(
            agent_name="support-bot",
            system="openai",
            request_model="gpt-4o",
            input_tokens=812,
            output_tokens=143,
        )
    """

    def __init__(
        self, http: HttpClient, *, service_name: Optional[str] = None
    ) -> None:
        self._http = http
        self._service_name = service_name

    def emit(self, resource_spans: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Emit a raw OTLP/HTTP ExportTraceServiceRequest
        (``{"resourceSpans": [...]}``). Validates the server bounds BEFORE
        sending so a too-large batch fails fast. Returns the ingest ack
        (``{"accepted": ..., "buffered": ...}``).

        Raises:
            ValueError: If the batch exceeds the resourceSpans / body-size caps.
        """
        if not isinstance(resource_spans, list):
            raise ValueError("emit() requires a resourceSpans list")
        if len(resource_spans) > OTLP_MAX_RESOURCE_SPANS:
            raise ValueError(
                f"Too many resourceSpans ({len(resource_spans)} > "
                f"{OTLP_MAX_RESOURCE_SPANS}); split into smaller batches."
            )
        body = {"resourceSpans": resource_spans}
        import json as _json

        if len(_json.dumps(body).encode("utf-8")) > OTLP_MAX_BODY_BYTES:
            raise ValueError(
                f"OTLP payload exceeds the {OTLP_MAX_BODY_BYTES}-byte body limit; "
                "reduce the batch size."
            )
        return self._http.post(OTLP_TRACES_PATH, json=body)

    def build_gen_ai_resource_spans(
        self, spans: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Build the OTLP resourceSpans for a set of GenAI spans WITHOUT sending
        them. The emitting service identity is stamped as ``service.name``.
        """
        resource_spans: dict[str, Any] = {
            "scopeSpans": [
                {
                    "scope": {
                        "name": "praesidia-python",
                        "version": _SDK_SCOPE_VERSION,
                    },
                    "spans": spans,
                }
            ]
        }
        if self._service_name:
            resource_spans["resource"] = {
                "attributes": [_str_attr(_SERVICE_NAME_ATTR, self._service_name)]
            }
        return [resource_spans]

    def emit_gen_ai_spans(self, spans: list[dict[str, Any]]) -> dict[str, Any]:
        """Emit one or more pre-built GenAI spans as a single OTLP batch."""
        return self.emit(self.build_gen_ai_resource_spans(spans))

    def emit_gen_ai_span(self, agent_name: str, **kwargs: Any) -> dict[str, Any]:
        """
        Build and emit a single GenAI-convention span. Accepts the same keyword
        arguments as :func:`gen_ai_span`. Returns the ingest ack.
        """
        return self.emit_gen_ai_spans([gen_ai_span(agent_name, **kwargs)])
