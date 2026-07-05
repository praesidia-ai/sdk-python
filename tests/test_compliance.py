"""Tests for praesidia.compliance.ComplianceResource (Q1-04)."""

from __future__ import annotations

import httpx
import pytest
import respx

from praesidia import Praesidia, PraesidiaError

BASE_URL = "http://test.local"
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
