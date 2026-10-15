"""N135 — the retry budget bounds the whole response body, not each read (TS AbortSignal parity).

A real local server sends headers, then trickles one byte every 50 ms. httpx's read timeout is
per read (idle), so only an explicit deadline check in the body loop stops it.
"""

from __future__ import annotations

import asyncio
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from praesidia import (
    AsyncPraesidiaInteractionHooks,
    Praesidia,
    PraesidiaInteractionHooks,
    RetryConfig,
)

DRIP_S = 3.0  # the server gives up after this, so a pre-fix run fails instead of hanging forever
BUDGET = RetryConfig(max_attempts=3, base_delay_s=0, max_delay_s=0, max_elapsed_s=0.3)


@pytest.fixture
def drip_url():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def handle_request(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "1000")
            self.end_headers()
            stop = time.monotonic() + DRIP_S
            try:
                while time.monotonic() < stop:
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.05)
            except OSError:
                pass  # the client hung up: the expected outcome

        do_GET = do_POST = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def _timed(fn):
    start = time.monotonic()
    with pytest.raises(httpx.ReadTimeout):
        fn()
    return time.monotonic() - start


@pytest.mark.parametrize("mode", ["sync", "async"])
def test_hook_post_body_drip_stops_at_the_budget(drip_url, mode):
    cls = PraesidiaInteractionHooks if mode == "sync" else AsyncPraesidiaInteractionHooks
    h = cls(api_key="pk_test", org_id="org-1", agent_id="agent-1", base_url=drip_url, timeout=5.0, retry=BUDGET)
    call = lambda: h.decide("agent_to_browser", {"name": "click", "target": {}})
    elapsed = _timed(call if mode == "sync" else lambda: asyncio.run(call()))
    assert elapsed < 1.0


def test_management_client_body_drip_stops_at_the_budget(drip_url):
    client = Praesidia(api_key="pk_test", org_id="org-1", base_url=drip_url, timeout=5.0, retry=BUDGET)
    assert _timed(lambda: client.agents.list()) < 1.0
