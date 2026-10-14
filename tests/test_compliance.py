"""Tests for praesidia.compliance.ComplianceResource (Q1-04)."""

from __future__ import annotations

import httpx
import pytest
import respx

from praesidia import Praesidia, PraesidiaError

BASE_URL = "https://test.local"
ORG_ID = "org-1"
REPORTS = f"{BASE_URL}/organizations/{ORG_ID}/compliance/eu-ai-act/reports"


def _client() -> Praesidia:
    return Praesidia(api_key="sk-test", org_id=ORG_ID, base_url=BASE_URL)


def _status(status: str, ready: bool, **extra):
    body = {
        "reportId": "rep-1",
        "status": status,
        "ready": ready,
        "pdfByteLength": 2048 if ready else None,
        "error": None,
        "requestedAt": "2026-07-05T00:00:00.000Z",
        "completedAt": "2026-07-05T00:01:00.000Z" if ready else None,
    }
    body.update(extra)
    return body


@respx.mock
def test_request_report():
    route = respx.post(REPORTS).mock(
        return_value=httpx.Response(
            200, json={"reportId": "rep-1", "jobId": "job-9", "status": "pending"}
        )
    )
    result = _client().compliance.request_report()

    assert route.called
    assert result["reportId"] == "rep-1"
    assert result["jobId"] == "job-9"
    assert result["status"] == "pending"


@respx.mock
def test_get_status():
    respx.get(f"{REPORTS}/rep-1").mock(
        return_value=httpx.Response(200, json=_status("completed", True))
    )
    status = _client().compliance.get_status("rep-1")

    assert status["ready"] is True
    assert status["pdfByteLength"] == 2048


@respx.mock
def test_get_json():
    doc = {"schemaVersion": "q1-04-v1", "reportId": "rep-1", "articleMatrix": []}
    respx.get(f"{REPORTS}/rep-1/json").mock(
        return_value=httpx.Response(200, json=doc)
    )
    result = _client().compliance.get_json("rep-1")

    assert result["schemaVersion"] == "q1-04-v1"


@respx.mock
def test_get_json_conflict_raises():
    respx.get(f"{REPORTS}/rep-1/json").mock(
        return_value=httpx.Response(409, text="Report not completed")
    )
    with pytest.raises(PraesidiaError):
        _client().compliance.get_json("rep-1")


@respx.mock
def test_get_pdf_returns_bytes():
    pdf = b"%PDF-1.7 fake"
    respx.get(f"{REPORTS}/rep-1/pdf").mock(
        return_value=httpx.Response(
            200, content=pdf, headers={"Content-Type": "application/pdf"}
        )
    )
    result = _client().compliance.get_pdf("rep-1")

    assert isinstance(result, bytes)
    assert result == pdf


@respx.mock
def test_wait_for_report_polls_until_ready():
    respx.get(f"{REPORTS}/rep-1").mock(
        side_effect=[
            httpx.Response(200, json=_status("processing", False)),
            httpx.Response(200, json=_status("processing", False)),
            httpx.Response(200, json=_status("completed", True)),
        ]
    )
    status = _client().compliance.wait_for_report(
        "rep-1", timeout=5.0, poll_interval=0.0
    )

    assert status["ready"] is True


@respx.mock
def test_wait_for_report_raises_on_failed():
    respx.get(f"{REPORTS}/rep-1").mock(
        return_value=httpx.Response(
            200, json=_status("failed", False, error="assembler exploded")
        )
    )
    with pytest.raises(PraesidiaError, match="assembler exploded"):
        _client().compliance.wait_for_report("rep-1", poll_interval=0.0)


@respx.mock
def test_wait_for_report_times_out():
    respx.get(f"{REPORTS}/rep-1").mock(
        return_value=httpx.Response(200, json=_status("processing", False))
    )
    with pytest.raises(TimeoutError):
        _client().compliance.wait_for_report(
            "rep-1", timeout=0.001, poll_interval=0.05
        )


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True, "5"])
def test_wait_for_report_rejects_invalid_timeout(timeout):
    with pytest.raises(ValueError, match="timeout"):
        _client().compliance.wait_for_report("rep-1", timeout=timeout)


@pytest.mark.parametrize(
    "poll_interval", [-1, float("nan"), float("inf"), True, "1"]
)
def test_wait_for_report_rejects_invalid_poll_interval(poll_interval):
    with pytest.raises(ValueError, match="poll_interval"):
        _client().compliance.wait_for_report(
            "rep-1", poll_interval=poll_interval
        )


@respx.mock
def test_generate_and_wait():
    respx.post(REPORTS).mock(
        return_value=httpx.Response(
            200, json={"reportId": "rep-1", "jobId": "job-9", "status": "pending"}
        )
    )
    respx.get(f"{REPORTS}/rep-1").mock(
        side_effect=[
            httpx.Response(200, json=_status("processing", False)),
            httpx.Response(200, json=_status("completed", True)),
        ]
    )
    status = _client().compliance.generate_and_wait(poll_interval=0.0)

    assert status["ready"] is True
    assert status["reportId"] == "rep-1"


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"timeout": 0}, "timeout"),
        ({"poll_interval": float("nan")}, "poll_interval"),
    ],
)
@respx.mock
def test_generate_and_wait_validates_before_enqueuing(kwargs, message):
    create_route = respx.post(REPORTS)

    with pytest.raises(ValueError, match=message):
        _client().compliance.generate_and_wait(**kwargs)

    assert not create_route.called


class _Clock:
    """Deterministic stand-in for the ``time`` module used by compliance.py."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr("praesidia.compliance.time", c)
    return c


@respx.mock
def test_wait_for_report_caps_request_timeout_to_remaining_deadline(clock):
    seen: list[float] = []

    def handler(request):
        seen.append(request.extensions["timeout"]["read"])
        clock.now += 1.0
        return httpx.Response(200, json=_status("processing", False))

    respx.get(f"{REPORTS}/rep-1").mock(side_effect=handler)
    with pytest.raises(TimeoutError):
        _client().compliance.wait_for_report("rep-1", timeout=3.0, poll_interval=0.5)

    assert seen == [3.0, 1.5]  # never the 30s client default, shrinks with the deadline


@respx.mock
def test_wait_for_report_rejects_ready_response_after_deadline(clock):
    def handler(request):
        clock.now += 5.0  # slow response lands after the 3s deadline
        return httpx.Response(200, json=_status("completed", True))

    route = respx.get(f"{REPORTS}/rep-1").mock(side_effect=handler)
    with pytest.raises(TimeoutError):
        _client().compliance.wait_for_report("rep-1", timeout=3.0, poll_interval=0.5)

    assert route.call_count == 1


@respx.mock
def test_wait_for_report_starts_no_poll_at_deadline(clock):
    route = respx.get(f"{REPORTS}/rep-1").mock(
        return_value=httpx.Response(200, json=_status("processing", False))
    )
    with pytest.raises(TimeoutError):
        _client().compliance.wait_for_report("rep-1", timeout=2.0, poll_interval=1.0)

    assert route.call_count == 2  # polls at t=0 and t=1; a poll at t=2 (the deadline) never starts
