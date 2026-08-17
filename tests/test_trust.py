"""Tests for praesidia.trust — fetch + OFFLINE-verify a trust passport (H3-02f).

The signed-passport fixture below was minted by Node's crypto (Ed25519) over the
SDK's own canonicalJson, so a passing verification here proves the pure-Python
Ed25519 + canonical-JSON in ``_crypto.py`` agree byte-for-byte with the be-core
signing path across languages.
"""

from __future__ import annotations

import copy

import httpx
import pytest
import respx

from praesidia import (
    Praesidia,
    ed25519_public_key_from_jwk,
    verify_passport,
)

BASE_URL = "http://test.local"
ORG_ID = "org-1"

# --- Cross-language fixture (Node-signed over the SDK canonicalJson) ---------
PASSPORT = {
    "@context": [
        "https://www.w3.org/2018/credentials/v1",
        "https://praesidia.ai/credentials/trust-passport/v1",
    ],
    "type": ["VerifiableCredential", "TrustPassport"],
    "id": "https://api.praesidia.ai/trust/passport/agent-1#2026-07-06T00:00:00.000Z",
    "issuer": "did:web:praesidia.ai:orgs:org-1",
    "issuanceDate": "2026-07-06T00:00:00.000Z",
    "expirationDate": "2999-07-07T00:00:00.000Z",
    "credentialSubject": {
        "id": "did:web:praesidia.ai:agents:agent-1",
        "agentName": "Nova",
        "trustLevel": "TRUSTED",
        "trustScore": 87,
        "posture": {"status": "verified", "expiresAt": None},
        "redTeam": {"completedRuns": 3, "lastTestedAt": None},
        "attestations": {
            "activeCount": 2,
            "identityVerified": True,
            "guardrailsActive": True,
            "auditTrailEnabled": True,
            "spendCapConfigured": True,
        },
        "compliance": ["EU-AI-Act", "GDPR"],
    },
    "proof": {
        "type": "Ed25519Signature2020",
        "created": "2026-07-06T00:00:00.000Z",
        "proofPurpose": "assertionMethod",
        "verificationMethod": "did:web:praesidia.ai:orgs:org-1#key-1",
        "keyVersion": 1,
        "proofValue": (
            "wRvMskdwsZ4lRuZQerCaO6+QtUs64vN8P6Itm5zEHei"
            "DY36QDuJXKWf7+8YPtdxnmeWbPSGSChj3+0o5d5daBQ=="
        ),
    },
}
PUBLIC_KEY_JWK = {
    "kty": "OKP",
    "crv": "Ed25519",
    "use": "sig",
    "x": "EAxCTATcxCZf-LxssFR99e6TaZa1Vj7yPWb6prZPv4c",
}


def _client() -> Praesidia:
    return Praesidia(api_key="sk-test", org_id=ORG_ID, base_url=BASE_URL)


# ── standalone offline verify (no client / account needed) ───────────────────


def test_verify_passport_accepts_genuine_signature():
    result = verify_passport(PASSPORT, PUBLIC_KEY_JWK)
    assert result["verified"] is True
    assert result["signatureValid"] is True
    assert result["reason"] == "ok"


def test_verify_passport_rejects_tampered_passport():
    tampered = copy.deepcopy(PASSPORT)
    tampered["credentialSubject"]["trustScore"] = 100
    result = verify_passport(tampered, PUBLIC_KEY_JWK)
    assert result["verified"] is False
    assert result["signatureValid"] is False
    assert result["reason"] == "signature-mismatch"


def test_verify_passport_fails_closed_on_wrong_key():
    wrong = dict(PUBLIC_KEY_JWK, x="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    result = verify_passport(PASSPORT, wrong)
    # Either malformed decode or signature mismatch — never "verified".
    assert result["verified"] is False


def test_verify_passport_reports_malformed_public_key():
    result = verify_passport(PASSPORT, {"kty": "EC"})
    assert result["reason"] == "malformed-public-key"


def test_verify_passport_reports_expired():
    expired = copy.deepcopy(PASSPORT)
    expired["expirationDate"] = "2000-01-01T00:00:00.000Z"
    result = verify_passport(expired, PUBLIC_KEY_JWK)
    # Signature is over the ORIGINAL doc, so mutating expiry invalidates it;
    # confirm the top-level verdict is still a fail.
    assert result["verified"] is False


def test_verify_passport_missing_proof():
    no_proof = {k: v for k, v in PASSPORT.items() if k != "proof"}
    result = verify_passport(no_proof, PUBLIC_KEY_JWK)
    assert result["reason"] == "missing-proof"


@pytest.mark.parametrize("expiration", [None, "", "not-a-date", "2999-01-01"])
def test_verify_passport_fails_closed_on_invalid_expiration(monkeypatch, expiration):
    passport = copy.deepcopy(PASSPORT)
    if expiration is None:
        passport.pop("expirationDate")
    else:
        passport["expirationDate"] = expiration
    monkeypatch.setattr("praesidia.trust.ed25519_verify", lambda *_args: True)

    result = verify_passport(passport, PUBLIC_KEY_JWK)

    assert result == {
        "verified": False,
        "signatureValid": True,
        "expired": False,
        "reason": "invalid-expiration",
    }


def test_verify_passport_never_raises_for_non_json_object_keys(monkeypatch):
    passport = copy.deepcopy(PASSPORT)
    passport[1] = "invalid JSON key"
    monkeypatch.setattr("praesidia.trust.ed25519_verify", lambda *_args: True)

    result = verify_passport(passport, PUBLIC_KEY_JWK)

    assert result["verified"] is False
    assert result["reason"] == "malformed-passport"


def test_verify_passport_rejects_noncanonical_base64_proof():
    passport = copy.deepcopy(PASSPORT)
    passport["proof"]["proofValue"] += "!ignored-by-lenient-decoders"

    result = verify_passport(passport, PUBLIC_KEY_JWK)

    assert result["verified"] is False
    assert result["reason"] == "signature-mismatch"


def test_ed25519_public_key_from_jwk_roundtrip():
    raw = ed25519_public_key_from_jwk(PUBLIC_KEY_JWK)
    assert raw is not None and len(raw) == 32
    assert ed25519_public_key_from_jwk({"kty": "EC"}) is None


def test_resource_verify_passport_delegates_to_offline_verifier():
    result = _client().trust.verify_passport(PASSPORT, PUBLIC_KEY_JWK)
    assert result["verified"] is True


def test_verify_passport_reports_valid_signature_on_expired_document(monkeypatch):
    expired = copy.deepcopy(PASSPORT)
    expired["expirationDate"] = "2000-01-01T00:00:00+00:00"
    monkeypatch.setattr("praesidia.trust.ed25519_verify", lambda *_args: True)

    result = verify_passport(expired, PUBLIC_KEY_JWK)

    assert result == {
        "verified": False,
        "signatureValid": True,
        "expired": True,
        "reason": "expired",
    }


# ── resource fetch + verify ──────────────────────────────────────────────────


@respx.mock
def test_fetch_and_verify_hits_public_route_and_verifies():
    bundle = {
        "passport": PASSPORT,
        "publicKeyJwk": PUBLIC_KEY_JWK,
        "didDocumentUrl": "https://api.praesidia.ai/agents/agent-1/did.json",
        "verificationHint": "Import publicKeyJwk...",
    }
    route = respx.get(f"{BASE_URL}/trust/passport/agent-1/verify").mock(
        return_value=httpx.Response(200, json=bundle)
    )
    result = _client().trust.fetch_and_verify("agent-1")
    assert route.called
    assert "Authorization" not in route.calls.last.request.headers
    assert result["verified"] is True
    assert result["passport"]["credentialSubject"]["agentName"] == "Nova"
    assert result["didDocumentUrl"].endswith("/agents/agent-1/did.json")


@respx.mock
def test_fetch_passport_returns_signed_credential():
    route = respx.get(f"{BASE_URL}/trust/passport/agent-1").mock(
        return_value=httpx.Response(200, json=PASSPORT)
    )
    passport = _client().trust.fetch_passport("agent-1")
    assert route.called
    assert "Authorization" not in route.calls.last.request.headers
    assert passport["proof"]["type"] == "Ed25519Signature2020"
