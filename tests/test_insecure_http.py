"""SDK-0339 — plaintext http: is refused except for loopback or an explicit opt-in."""

from __future__ import annotations

import pytest

from praesidia import Praesidia, PraesidiaConfigError, Guard, PraesidiaInteractionHooks, PraesidiaTrust
from praesidia._http import normalize_base_url

INSECURE = "http://api.example.com"
CFG = {"api_key": "pk_test", "org_id": "org"}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("PRAESIDIA_ALLOW_INSECURE_HTTP", raising=False)
    monkeypatch.delenv("PRAESIDIA_BASE_URL", raising=False)


@pytest.mark.parametrize("url", ["https://api.example.com", "https://10.0.0.5:8443/v1"])
def test_https_any_host_ok(url):
    assert normalize_base_url(url) == url


@pytest.mark.parametrize(
    "url", ["http://localhost:5001", "http://127.0.0.1", "http://127.8.9.10:80", "http://[::1]:5001", "http://LOCALHOST"]
)
def test_loopback_http_ok(url):
    normalize_base_url(url)


@pytest.mark.parametrize(
    "url",
    [INSECURE, "http://api.localhost", "http://0.0.0.0:5001", "http://128.0.0.1", "http://127.0.0.1.example.com", "http://[::2]"],
)
def test_non_loopback_http_is_config_error(url):
    with pytest.raises(PraesidiaConfigError, match="allow_insecure_http"):
        normalize_base_url(url)


def test_opt_in_allows_non_loopback_http(monkeypatch):
    assert normalize_base_url(INSECURE, allow_insecure_http=True) == INSECURE
    monkeypatch.setenv("PRAESIDIA_ALLOW_INSECURE_HTTP", "1")
    assert normalize_base_url(INSECURE) == INSECURE
    with pytest.raises(PraesidiaConfigError):
        normalize_base_url(INSECURE, allow_insecure_http=False)


def test_rule_holds_on_every_constructor_and_env_default(monkeypatch):
    with pytest.raises(PraesidiaConfigError):
        Praesidia(base_url=INSECURE, **CFG)
    with pytest.raises(PraesidiaConfigError):
        Guard(base_url=INSECURE, **CFG)
    with pytest.raises(PraesidiaConfigError):
        PraesidiaTrust(base_url=INSECURE)
    with pytest.raises(PraesidiaConfigError):
        PraesidiaInteractionHooks(base_url=INSECURE, agent_id="agent", **CFG)
    monkeypatch.setenv("PRAESIDIA_BASE_URL", INSECURE)
    with pytest.raises(PraesidiaConfigError):
        Guard(**CFG)
    Praesidia(base_url=INSECURE, allow_insecure_http=True, **CFG)
    Guard(allow_insecure_http=True, **CFG)
    PraesidiaTrust(allow_insecure_http=True)
    PraesidiaInteractionHooks(agent_id="agent", allow_insecure_http=True, **CFG).close()
