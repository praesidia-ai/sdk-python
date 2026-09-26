"""SDK-0349 -- parity with TS SDK-0348.

Only an outage (transport error, timeout, 408, 5xx) may degrade the guard to
local rules. Any other 4xx -- including 429 -- is caller-triggerable (oversized
content, a shared egress IP hitting the throttle) and must never switch the
org's guardrails off, so it raises in every ``failure_mode``. Oversized content
is rejected locally before any request.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from praesidia.exceptions import PraesidiaError
from praesidia.guard import Guard

BASE_URL = "https://test.local"
ORG_ID = "org-uuid-123"
VALIDATE = f"{BASE_URL}/organizations/{ORG_ID}/guardrails/validate"
TASKS = f"{BASE_URL}/organizations/{ORG_ID}/tasks"
MODES = ["local_rules", "fail_open"]


def _guard(mode: str) -> Guard:
    return Guard(
        api_key="pk_test_key", org_id=ORG_ID, base_url=BASE_URL, retry=False,
        failure_mode=mode, connection_id="00000000-0000-4000-8000-000000000c01",
    )


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 429])
@respx.mock
def test_caller_triggerable_4xx_raises_instead_of_degrading(mode, status):
    respx.post(VALIDATE).mock(return_value=httpx.Response(status, json={"message": "no"}))
    guard = _guard(mode)
    with pytest.raises(PraesidiaError) as exc:
        guard.check_input("hello")
    assert exc.value.status_code == status
    with pytest.raises(PraesidiaError):
        guard.run(lambda: "never", input="hello")


@pytest.mark.parametrize("mode", MODES)
@respx.mock
def test_log_task_4xx_raises(mode):
    respx.post(TASKS).mock(return_value=httpx.Response(400, json={"message": "bad"}))
    with pytest.raises(PraesidiaError):
        _guard(mode).log_task({"input": "x", "output": "y"})


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize(
    "side_effect",
    [
        httpx.ConnectError("down"),
        httpx.ReadTimeout("slow"),
        httpx.Response(408, json={"message": "timeout"}),
        httpx.Response(503, json={"message": "unavailable"}),
    ],
    ids=["connect", "timeout", "408", "503"],
)
@respx.mock
def test_outage_degrades(mode, side_effect):
    route = respx.post(VALIDATE)
    if isinstance(side_effect, httpx.Response):
        route.mock(return_value=side_effect)
    else:
        route.mock(side_effect=side_effect)
    result = _guard(mode).check_input("hello")
    assert result["degraded"] is True and result["local"] is True


@respx.mock
def test_oversized_content_rejected_locally_without_a_request():
    from praesidia import MAX_GUARD_CONTENT_LENGTH, GuardContentTooLargeError

    assert MAX_GUARD_CONTENT_LENGTH == 100_000
    route = respx.post(VALIDATE).mock(return_value=httpx.Response(200, json={"passed": True}))
    guard = _guard("local_rules")
    # code points, as the server counts: 100_001 astral chars is 200_002 UTF-16 units
    for content in ("a" * (MAX_GUARD_CONTENT_LENGTH + 1), "\U0001F600" * (MAX_GUARD_CONTENT_LENGTH + 1)):
        with pytest.raises(GuardContentTooLargeError) as exc:
            guard.check_output(content)
        assert exc.value.code == "CONTENT_TOO_LARGE"
        assert exc.value.length == MAX_GUARD_CONTENT_LENGTH + 1
        assert exc.value.max_length == MAX_GUARD_CONTENT_LENGTH
    assert not route.called
    assert guard.check_input("\U0001F600" * MAX_GUARD_CONTENT_LENGTH)["local"] is False
