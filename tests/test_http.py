"""Tests for praesidia._http.stream_get timeout handling (BUGHUNT-SDK-06).

stream_get backs the bulk download/export calls (report PDF, audit export,
analytics export). It must use a finite per-operation timeout so a stalled
server fails fast instead of hanging the client forever.
"""

from __future__ import annotations

import httpx
import pytest

from praesidia import Praesidia
from praesidia._http import HttpClient, _DOWNLOAD_TIMEOUT, path_segment


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


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "api.example.test",
        "ftp://api.example.test",
        "https://user:secret@api.example.test",
        "https://api.example.test?redirect=evil",
        "https://api.example.test/#fragment",
        " https://api.example.test",
    ],
)
def test_rejects_unsafe_base_urls(base_url):
    with pytest.raises(ValueError, match="base_url"):
        HttpClient(api_key="k", org_id="o", base_url=base_url)


@pytest.mark.parametrize("api_key", ["", "   ", "key\r\ninjected: true"])
def test_rejects_invalid_api_keys(api_key):
    with pytest.raises(ValueError, match="api_key"):
        HttpClient(api_key=api_key, org_id="o", base_url="https://api.test")


@pytest.mark.parametrize("timeout", [0, -1, 301, float("inf"), True, "30"])
def test_rejects_invalid_request_timeouts(timeout):
    with pytest.raises(ValueError, match="timeout"):
        HttpClient(
            api_key="k",
            org_id="o",
            base_url="https://api.test",
            timeout=timeout,
        )


def test_general_requests_use_configured_timeout(monkeypatch):
    captured: dict = {}

    def fake_get(url, **kwargs):
        captured.update(kwargs)
        return httpx.Response(200, json={}, request=httpx.Request("GET", url))

    monkeypatch.setattr("praesidia._http.httpx.get", fake_get)
    client = HttpClient(
        api_key="k", org_id="o", base_url="http://test.local", timeout=7.5
    )
    client.get("/health")
    assert captured["timeout"] == 7.5


def test_path_segments_are_encoded_and_empty_values_rejected():
    assert path_segment("../admin/a b", "resource_id") == "..%2Fadmin%2Fa%20b"
    with pytest.raises(ValueError, match="resource_id"):
        path_segment("", "resource_id")


def test_public_client_exposes_matching_package_version():
    import praesidia

    assert praesidia.__version__ == "0.2.0"
    assert Praesidia(api_key="k", org_id="o")._http._timeout == 30.0
