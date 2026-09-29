"""SDK-0313 — tag gateway calls with the MCP server they are made for (GW-0776).

The gateway's client is the OpenAI or Anthropic SDK pointed at it. Pass
``gateway_headers(...)`` as ``default_headers`` (per client) and/or
``extra_headers`` (per call); the per-call dict replaces the client value.
"""

from __future__ import annotations

import re

from .exceptions import InvalidMcpServerIdError

#: Optional and untrusted: be records it only for an MCP server the calling key's
#: org owns. The gateway strips it before forwarding upstream.
MCP_SERVER_ID_HEADER = "x-praesidia-mcp-server-id"

# The gateway's ``is_uuid_shaped`` (gateway-server proxy.rs): canonical hyphenated
# 8-4-4-4-12 hex in either case, version/variant nibbles unconstrained.
_UUID_SHAPE = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")


def gateway_headers(*, mcp_server_id: str | None = None) -> dict[str, str]:
    """Return the gateway request headers; ``{}`` when no MCP server id is given.

    Raises ``InvalidMcpServerIdError`` (a ``PraesidiaConfigError``) when the id
    is not one UUID, so a malformed value never reaches the wire.
    """
    if mcp_server_id is None:
        return {}
    if not isinstance(mcp_server_id, str) or not _UUID_SHAPE.fullmatch(mcp_server_id):
        raise InvalidMcpServerIdError()
    return {MCP_SERVER_ID_HEADER: mcp_server_id}
