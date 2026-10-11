"""be validates these enums; an installed SDK must not block a value be adds later.

List-filter enums (agents, connections, ai_systems) are covered in test_management_query_contract.py.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from praesidia import Praesidia

BASE = "https://test.local/organizations/org-1"


def client() -> Praesidia:
    return Praesidia(
        api_key="pk_test", org_id="org-1", base_url="https://test.local", retry=False
    )


@respx.mock
@pytest.mark.parametrize(
    "call,key",
    [
        (lambda c: c.connections.update_status("c1", "FUTURE"), "status"),
        (lambda c: c.memory.create("ok", source_type="FUTURE"), "sourceType"),
        (lambda c: c.memory.list(source_type="FUTURE"), "sourceType"),
        (lambda c: c.audit.export(format="FUTURE"), "format"),
    ],
    ids=[
        "connections.update_status",
        "memory.create.source_type",
        "memory.list.source_type",
        "audit.format",
    ],
)
def test_unknown_enum_value_is_sent_unchanged(call, key):
    route = respx.route(url__startswith=BASE).mock(
        return_value=httpx.Response(200, json=[])
    )
    call(client())
    request = route.calls.last.request
    body = json.loads(request.content) if request.content else {}
    assert request.url.params.get(key, body.get(key)) == "FUTURE"
