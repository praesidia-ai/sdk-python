"""Tests for praesidia._http.stream_get timeout handling (BUGHUNT-SDK-06).

stream_get backs the bulk download/export calls (report PDF, audit export,
analytics export). It must use a finite per-operation timeout so a stalled
server fails fast instead of hanging the client forever.
"""

from __future__ import annotations

import gzip

import httpx
import pytest

from praesidia import Praesidia, ResponseTooLargeError
from praesidia._http import (
    HttpClient,
    _DOWNLOAD_TIMEOUT,
    _MAX_ERROR_RESPONSE_BYTES,
    _MAX_JSON_RESPONSE_BYTES,
    path_segment,
)
from praesidia.exceptions import (
    AuthError,
    ForbiddenError,
    NotFoundError,
    PraesidiaError,
    RateLimitError,
    ServerError,
)


def _patch_bounded_request(monkeypatch, method, fake_request):
    def bounded(_self, actual_method, url, **kwargs):
        assert actual_method == method
        kwargs.pop("path")
        kwargs.pop("success_limit")
        return fake_request(url, **kwargs)

    monkeypatch.setattr(HttpClient, "_request_bounded", bounded)


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

    _patch_bounded_request(monkeypatch, "GET", fake_get)
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

    _patch_bounded_request(monkeypatch, "GET", fake_get)
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
        "https://api example.test",
        "https:\\evil.example.test",
        "https://api.example.test:99999",
        "https://api.example.test/\x00",
    ],
)
def test_rejects_unsafe_base_urls(base_url):
    with pytest.raises(ValueError, match="base_url"):
        HttpClient(api_key="k", org_id="o", base_url=base_url)


@pytest.mark.parametrize(
    "api_key", ["", "   ", " key", "key ", "key\tvalue", "key\r\ninjected: true"]
)
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

    _patch_bounded_request(monkeypatch, "GET", fake_get)
    client = HttpClient(
        api_key="k", org_id="o", base_url="http://test.local", timeout=7.5
    )
    client.get("/health")
    assert captured["timeout"] == 7.5


def test_bounded_request_rejects_declared_body_before_reading(monkeypatch):
    response = httpx.Response(
        200,
        headers={"Content-Length": str(_MAX_JSON_RESPONSE_BYTES + 1)},
        stream=httpx.ByteStream(b"{}"),
        request=httpx.Request("GET", "https://api.test/large"),
    )

    class StreamContext:
        def __enter__(self):
            return response

        def __exit__(self, *_args):
            response.close()

    monkeypatch.setattr(httpx, "stream", lambda *_args, **_kwargs: StreamContext())
    client = HttpClient(api_key="k", org_id="o", base_url="https://api.test")

    with pytest.raises(ResponseTooLargeError, match="16777216-byte limit") as exc:
        client.get("/large")

    assert exc.value.status_code == 200
    assert exc.value.path == "/large"


def test_bounded_request_counts_streamed_bytes_without_content_length(monkeypatch):
    class ChunkStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b"123"
            yield b"45"

    response = httpx.Response(
        200,
        stream=ChunkStream(),
        request=httpx.Request("GET", "https://api.test/stream"),
    )

    class StreamContext:
        def __enter__(self):
            return response

        def __exit__(self, *_args):
            response.close()

    monkeypatch.setattr(httpx, "stream", lambda *_args, **_kwargs: StreamContext())
    client = HttpClient(api_key="k", org_id="o", base_url="https://api.test")

    with pytest.raises(ResponseTooLargeError, match="4-byte limit"):
        client._request_bounded(
            "GET",
            "https://api.test/stream",
            path="/stream",
            success_limit=4,
        )


def test_error_response_uses_smaller_body_limit(monkeypatch):
    response = httpx.Response(
        502,
        headers={"Content-Length": str(_MAX_ERROR_RESPONSE_BYTES + 1)},
        stream=httpx.ByteStream(b"failure"),
        request=httpx.Request("GET", "https://api.test/failure"),
    )

    class StreamContext:
        def __enter__(self):
            return response

        def __exit__(self, *_args):
            response.close()

    monkeypatch.setattr(httpx, "stream", lambda *_args, **_kwargs: StreamContext())
    client = HttpClient(api_key="k", org_id="o", base_url="https://api.test")

    with pytest.raises(ResponseTooLargeError, match="65536-byte limit") as exc:
        client.get("/failure")

    assert exc.value.status_code == 502


def test_bounded_request_normalizes_headers_after_stream_decompression(monkeypatch):
    compressed = gzip.compress(b'{"ok":true}')
    response = httpx.Response(
        200,
        headers={
            "Content-Encoding": "gzip",
            "Content-Length": str(len(compressed)),
        },
        stream=httpx.ByteStream(compressed),
        request=httpx.Request("GET", "https://api.test/compressed"),
    )

    class StreamContext:
        def __enter__(self):
            return response

        def __exit__(self, *_args):
            response.close()

    monkeypatch.setattr(httpx, "stream", lambda *_args, **_kwargs: StreamContext())
    client = HttpClient(api_key="k", org_id="o", base_url="https://api.test")

    result = client._request_bounded(
        "GET",
        "https://api.test/compressed",
        path="/compressed",
        success_limit=1024,
    )

    assert result.json() == {"ok": True}
    assert "content-encoding" not in result.headers
    assert result.headers["content-length"] == str(len(result.content))


def test_path_segments_are_encoded_and_unsafe_values_rejected():
    assert path_segment("../admin/a b", "resource_id") == "..%2Fadmin%2Fa%20b"
    for unsafe in ("", " ", " id", ".", ".."):
        with pytest.raises(ValueError, match="resource_id"):
            path_segment(unsafe, "resource_id")


def test_public_client_exposes_matching_package_version():
    import praesidia

    assert praesidia.__version__ == "0.4.1"
    assert Praesidia(api_key="k", org_id="o")._http._timeout == 30.0


@pytest.mark.parametrize(
    "chain_id",
    [" chain-1", "chain-1 ", "chain\t1", "chain\x001", 123],
)
def test_forward_chain_rejects_values_unsafe_for_http_headers(chain_id):
    client = Praesidia(api_key="k", org_id="o")

    with pytest.raises(ValueError, match="chain_id"):
        client.forward_chain(chain_id)


@pytest.mark.parametrize(
    ("status", "exception"),
    [
        (400, PraesidiaError),
        (401, AuthError),
        (403, ForbiddenError),
        (404, NotFoundError),
        (429, RateLimitError),
        (500, ServerError),
    ],
)
def test_http_statuses_map_to_typed_sdk_errors(status, exception):
    request = httpx.Request("GET", "https://api.test/resource")
    response = httpx.Response(status, text="failure", request=request)
    client = HttpClient(api_key="k", org_id="o", base_url="https://api.test")

    with pytest.raises(exception) as exc_info:
        client._raise_for_status(response)

    assert exc_info.value.status_code == status


def test_get_chain_id_reports_current_forwarded_chain():
    client = HttpClient(api_key="k", org_id="o", base_url="https://api.test")
    assert client.get_chain_id() is None
    client.set_chain_id("chain-1")
    assert client.get_chain_id() == "chain-1"
