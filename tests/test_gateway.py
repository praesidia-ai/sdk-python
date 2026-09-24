"""SDK-0313 — tag OpenAI-wire gateway calls with an MCP server id (GW-0776).

The gateway's client is the vendor SDK (OpenAI / Anthropic) pointed at it; both
send through an ``httpx`` client built from ``default_headers`` (per client)
merged under ``extra_headers`` (per call). These tests drive that same httpx
merge on the wire.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from praesidia import (
    MCP_SERVER_ID_HEADER,
    InvalidMcpServerIdError,
    PraesidiaConfigError,
    gateway_headers,
)

URL = "http://gateway.test/openai/v1/chat/completions"
CLIENT_ID = "018f4f1a-6b1e-7c3a-9d2e-abcdef123456"
CALL_ID = "018F4F1A-6B1E-7C3A-9D2E-ABCDEF654321"


@respx.mock
def test_no_id_sends_no_header():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={}))
    assert gateway_headers() == {}
    with httpx.Client(headers=gateway_headers()) as client:
        client.post(URL, json={})
    assert MCP_SERVER_ID_HEADER not in route.calls.last.request.headers


@respx.mock
def test_client_id_on_the_wire_and_per_call_id_wins():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={}))
    with httpx.Client(headers=gateway_headers(mcp_server_id=CLIENT_ID)) as client:
        client.post(URL, json={})
        client.post(URL, json={}, headers=gateway_headers(mcp_server_id=CALL_ID))
    first, second = (call.request.headers for call in route.calls)
    assert first.get_list(MCP_SERVER_ID_HEADER) == [CLIENT_ID]
    assert second.get_list(MCP_SERVER_ID_HEADER) == [CALL_ID]


@pytest.mark.parametrize(
    "bad",
    [
        "018f4f1a6b1e7c3a9d2eabcdef123456",  # no hyphens
        "018f4f1a-6b1e-7c3a-9d2e-abcdef12345",  # 35 chars
        "018f4f1a-6b1e-7c3a-9d2e-abcdef1234gg",  # non-hex
        f"{CLIENT_ID}\n",  # trailing newline
        f" {CLIENT_ID}",  # padded
        "",
        "mcp-server-id",
        123,
    ],
)
@respx.mock
def test_non_uuid_is_rejected_before_sending(bad):
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={}))
    with pytest.raises(InvalidMcpServerIdError) as exc:
        with httpx.Client() as client:
            client.post(URL, json={}, headers=gateway_headers(mcp_server_id=bad))
    assert isinstance(exc.value, PraesidiaConfigError)
    assert not route.called
