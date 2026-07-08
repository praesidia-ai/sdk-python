"""Tests for praesidia._http.stream_get timeout handling (BUGHUNT-SDK-06).

stream_get backs the bulk download/export calls (report PDF, audit export,
analytics export). It must use a finite per-operation timeout so a stalled
server fails fast instead of hanging the client forever.
"""

from __future__ import annotations

import httpx

from praesidia._http import HttpClient, _DOWNLOAD_TIMEOUT


def test_download_timeout_is_finite_on_every_phase():
    # No phase may be None — a None timeout disables that operation's budget
    # and lets a stalled peer hang the process indefinitely.
    assert isinstance(_DOWNLOAD_TIMEOUT, httpx.Timeout)
    assert _DOWNLOAD_TIMEOUT.connect is not None
    assert _DOWNLOAD_TIMEOUT.read is not None
    assert _DOWNLOAD_TIMEOUT.write is not None
    assert _DOWNLOAD_TIMEOUT.pool is not None


def test_stream_get_passes_finite_timeout(monkeypatch):
    captured: dict = {}

    def fake_get(url, **kwargs):
        captured.update(kwargs)
        return httpx.Response(200, content=b"ok")

    monkeypatch.setattr("praesidia._http.httpx.get", fake_get)
    client = HttpClient(api_key="k", org_id="o", base_url="http://test.local")
    client.stream_get("/reports/rep-1/pdf")

    # The download call must NOT disable timeouts (the old timeout=None bug).
    assert captured["timeout"] is not None
    assert captured["timeout"] is _DOWNLOAD_TIMEOUT


def test_stream_get_respects_explicit_timeout_override(monkeypatch):
    captured: dict = {}

    def fake_get(url, **kwargs):
        captured.update(kwargs)
        return httpx.Response(200, content=b"ok")

    monkeypatch.setattr("praesidia._http.httpx.get", fake_get)
    client = HttpClient(api_key="k", org_id="o", base_url="http://test.local")
    override = httpx.Timeout(5.0)
    client.stream_get("/x", timeout=override)

    assert captured["timeout"] is override
