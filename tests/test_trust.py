"""Tests for praesidia.trust — fetch + OFFLINE-verify a trust passport (H3-02f).

The signed-passport fixture below was minted by Node's crypto (Ed25519) over the
SDK's own canonicalJson, so a passing verification here proves the pure-Python
Ed25519 + canonical-JSON in ``_crypto.py`` agree byte-for-byte with the be-core
signing path across languages.
"""

from __future__ import annotations

import base64
import copy

import httpx
import pytest
import respx

from praesidia import (
    Praesidia,
    ed25519_public_key_from_jwk,
    jwk_thumbprint,
    jwk_thumbprint_hex,
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
P256_PUBLIC_KEY_JWK = {
    "kty": "EC",
    "crv": "P-256",
    "x": "xSg2U6IWcdbtUsOx6Re8wDnS_NsEsQmdbSgl5EtpLxE",
    "y": "M-6XtNyBIRsfx2li1AIOuWp_bYidD9bZpbX31VfuMgc",
    "use": "sig",
    "alg": "ES256",
}
P256_SIGNATURE_B64 = (
    "MEQCIHtQ3l2r2JAB9FeCBmCzyZD2VfOansvrvfH0l/1LoPyX"
    "AiAceQ6W0YewFp4SYoQm4QurVIgZIS+cSoqllyj2VAoJAQ=="
)
P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


def _p256_passport():
    passport = copy.deepcopy(PASSPORT)
    passport["proof"]["type"] = "EcdsaSecp256r1Signature2019"
    passport["proof"]["proofValue"] = P256_SIGNATURE_B64
    return passport


def _encode_der_integer(value):
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    if raw[0] & 0x80:
        raw = b"\x00" + raw
    return b"\x02" + bytes([len(raw)]) + raw


def _high_s_signature():
    signature = base64.b64decode(P256_SIGNATURE_B64, validate=True)
    r_length = signature[3]
    r_start = 4
    r_end = r_start + r_length
    s_length = signature[r_end + 1]
    s_start = r_end + 2
    r = int.from_bytes(signature[r_start:r_end], "big")
    s = int.from_bytes(signature[s_start : s_start + s_length], "big")
    body = _encode_der_integer(r) + _encode_der_integer(P256_N - s)
    return base64.b64encode(b"\x30" + bytes([len(body)]) + body).decode("ascii")


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


def test_verify_passport_accepts_genuine_kms_p256_signature():
    result = verify_passport(_p256_passport(), P256_PUBLIC_KEY_JWK)
    assert result == {
        "verified": True,
        "signatureValid": True,
        "expired": False,
        "reason": "ok",
    }


def test_verify_passport_rejects_tampered_or_high_s_p256_signature():
    tampered = _p256_passport()
    tampered["credentialSubject"]["trustScore"] = 100
    assert verify_passport(tampered, P256_PUBLIC_KEY_JWK)["reason"] == "signature-mismatch"

    malleable = _p256_passport()
    malleable["proof"]["proofValue"] = _high_s_signature()
    assert verify_passport(malleable, P256_PUBLIC_KEY_JWK)["reason"] == "signature-mismatch"


def test_verify_passport_rejects_proof_key_algorithm_confusion():
    passport = _p256_passport()
    passport["proof"]["type"] = "Ed25519Signature2020"
    assert verify_passport(passport, P256_PUBLIC_KEY_JWK)["reason"] == "signature-mismatch"


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

    oversized = copy.deepcopy(PASSPORT)
    oversized["proof"]["proofValue"] = "A" * 100
    assert verify_passport(oversized, PUBLIC_KEY_JWK)["reason"] == "signature-mismatch"


def test_ed25519_public_key_from_jwk_roundtrip():
    raw = ed25519_public_key_from_jwk(PUBLIC_KEY_JWK)
    assert raw is not None and len(raw) == 32
    assert ed25519_public_key_from_jwk({"kty": "EC"}) is None


def test_resource_verify_passport_delegates_to_offline_verifier():
    result = _client().trust.verify_passport(PASSPORT, PUBLIC_KEY_JWK)
    assert result["verified"] is True


def test_verify_passport_reports_valid_signature_on_expired_document(monkeypatch):
    expired = copy.deepcopy(PASSPORT)
    expired["expirationDate"] = "2026-07-07T00:00:00.000Z"
    monkeypatch.setattr("praesidia.trust.ed25519_verify", lambda *_args: True)

    result = verify_passport(expired, PUBLIC_KEY_JWK)

    assert result == {
        "verified": False,
        "signatureValid": True,
        "expired": True,
        "reason": "expired",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("proofPurpose", "authentication"),
        ("created", "2026-07-06T00:00:01.000Z"),
        ("keyVersion", 0),
        ("verificationMethod", "did:web:attacker.example#key-1"),
    ],
)
def test_verify_passport_rejects_tampered_proof_metadata(field, value):
    passport = copy.deepcopy(PASSPORT)
    passport["proof"][field] = value
    result = verify_passport(passport, PUBLIC_KEY_JWK)
    assert result["signatureValid"] is False
    assert result["reason"] == "malformed-passport"


def test_verify_passport_rejects_malformed_signed_subject():
    passport = copy.deepcopy(PASSPORT)
    passport["credentialSubject"]["attestations"]["activeCount"] = -1
    assert verify_passport(passport, PUBLIC_KEY_JWK)["reason"] == "malformed-passport"


def test_verify_passport_never_raises_for_hostile_mapping():
    class HostileDict(dict):
        def get(self, *_args, **_kwargs):
            raise RuntimeError("hostile getter")

    result = verify_passport(HostileDict(), {})
    assert result == {
        "verified": False,
        "signatureValid": False,
        "expired": False,
        "reason": "malformed-passport",
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
    # MCPSDK-04 — this assertion used to pass with NO anchor, which was the
    # bug: the key came from the same response. It now needs a pinned key.
    result = _client().trust.fetch_and_verify(
        "agent-1", trusted_keys=[PUBLIC_KEY_JWK]
    )
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


# ── fetch_and_verify trust anchor (SEC-2026-09-12 MCPSDK-04) ─────────────────

OTHER_KEY_JWK = {
    "kty": "OKP",
    "crv": "Ed25519",
    "use": "sig",
    # A different, unrelated Ed25519 public key.
    "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo",
}


def _mock_bundle(passport=None, public_key_jwk=None):
    respx.get(f"{BASE_URL}/trust/passport/agent-1/verify").mock(
        return_value=httpx.Response(
            200,
            json={
                "passport": passport if passport is not None else PASSPORT,
                "publicKeyJwk": (
                    public_key_jwk if public_key_jwk is not None else PUBLIC_KEY_JWK
                ),
                "didDocumentUrl": (
                    "https://api.praesidia.ai/agents/agent-1/did.json"
                ),
            },
        )
    )
    return _client().trust


@respx.mock
def test_fetch_and_verify_without_anchor_is_not_an_assurance():
    # The key came from the same unauthenticated GET as the passport, so the
    # signature check proves integrity only — never authenticity.
    result = _mock_bundle().fetch_and_verify("agent-1")
    assert result["verified"] is False
    assert result["reason"] == "unpinned_key"
    # ...but the signature state stays truthful.
    assert result["signatureValid"] is True
    assert result["expired"] is False
    assert result["publicKeyJwk"] == PUBLIC_KEY_JWK


@respx.mock
def test_fetch_and_verify_without_anchor_still_reports_broken_signature():
    tampered = copy.deepcopy(PASSPORT)
    tampered["credentialSubject"]["trustScore"] = 99
    result = _mock_bundle(passport=tampered).fetch_and_verify("agent-1")
    assert result["verified"] is False
    assert result["signatureValid"] is False
    assert result["reason"] == "signature-mismatch"


@respx.mock
def test_fetch_and_verify_with_matching_trusted_key():
    result = _mock_bundle().fetch_and_verify(
        "agent-1", trusted_keys=[PUBLIC_KEY_JWK]
    )
    assert result["verified"] is True
    assert result["signatureValid"] is True
    assert result["expired"] is False
    assert result["reason"] == "ok"


@respx.mock
def test_fetch_and_verify_accepts_mapping_of_trusted_keys():
    result = _mock_bundle().fetch_and_verify(
        "agent-1",
        trusted_keys={"key-0": OTHER_KEY_JWK, "key-1": PUBLIC_KEY_JWK},
    )
    assert result["verified"] is True
    assert result["reason"] == "ok"


@respx.mock
def test_fetch_and_verify_rejects_key_outside_the_anchor():
    # The attacker controls the response: passport + matching key are both
    # theirs. Under the old code this returned verified: True.
    result = _mock_bundle().fetch_and_verify(
        "agent-1", trusted_keys=[OTHER_KEY_JWK]
    )
    assert result["verified"] is False
    assert result["signatureValid"] is False
    assert result["reason"] == "untrusted_key"


@respx.mock
def test_fetch_and_verify_empty_trusted_keys_is_a_failed_anchor():
    result = _mock_bundle().fetch_and_verify("agent-1", trusted_keys=[])
    assert result["verified"] is False
    assert result["reason"] == "untrusted_key"


@respx.mock
def test_fetch_and_verify_keeps_expiry_reason_under_a_trusted_key(monkeypatch):
    expired = copy.deepcopy(PASSPORT)
    expired["expirationDate"] = "2026-07-07T00:00:00.000Z"
    # Same shortcut as the standalone expiry test: the fixture signature covers
    # expirationDate, so stub the signature check rather than re-signing.
    monkeypatch.setattr("praesidia.trust.ed25519_verify", lambda *_args: True)

    result = _mock_bundle(passport=expired).fetch_and_verify(
        "agent-1", trusted_keys=[PUBLIC_KEY_JWK]
    )

    assert result["verified"] is False
    assert result["signatureValid"] is True
    assert result["expired"] is True
    assert result["reason"] == "expired"


@respx.mock
def test_fetch_and_verify_accepts_matching_expected_fingerprint():
    for fingerprint in (
        jwk_thumbprint(PUBLIC_KEY_JWK),
        jwk_thumbprint_hex(PUBLIC_KEY_JWK),
        f"sha256:{jwk_thumbprint_hex(PUBLIC_KEY_JWK)}",
    ):
        result = _mock_bundle().fetch_and_verify(
            "agent-1", expected_fingerprint=fingerprint
        )
        assert result["verified"] is True, fingerprint
        assert result["reason"] == "ok"


@respx.mock
def test_fetch_and_verify_rejects_unpinned_fingerprint():
    result = _mock_bundle().fetch_and_verify(
        "agent-1", expected_fingerprint=jwk_thumbprint(OTHER_KEY_JWK)
    )
    assert result["verified"] is False
    assert result["reason"] == "fingerprint_mismatch"
    # The served key does sign this passport — that is exactly why the
    # self-referential check was worthless.
    assert result["signatureValid"] is True


def test_jwk_thumbprint_is_rfc7638_and_none_for_unusable_keys():
    import hashlib
    import json

    expected = hashlib.sha256(
        json.dumps(
            {"crv": "Ed25519", "kty": "OKP", "x": PUBLIC_KEY_JWK["x"]},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).digest()
    assert jwk_thumbprint(PUBLIC_KEY_JWK) == base64.urlsafe_b64encode(
        expected
    ).decode("ascii").rstrip("=")
    assert jwk_thumbprint_hex(PUBLIC_KEY_JWK) == expected.hex()
    assert jwk_thumbprint(P256_PUBLIC_KEY_JWK) is not None
    assert jwk_thumbprint({"kty": "RSA", "n": "x", "e": "AQAB"}) is None
