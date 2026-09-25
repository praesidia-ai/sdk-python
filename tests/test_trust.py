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
    NotFoundError,
    Praesidia,
    PraesidiaTrust,
    ServerError,
    ed25519_public_key_from_jwk,
    jwk_thumbprint,
    jwk_thumbprint_hex,
    verify_ai_system_passport,
    verify_passport,
)
from praesidia.trust import TrustResource

BASE_URL = "https://test.local"
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


# ── SDK-0306: AI System trust-passport PDF (BE-0541, binary response) ─────────

# A real PDF header + the high-bit "binary marker" comment line: any text/JSON
# decode on the way through would corrupt these bytes.
PDF_BYTES = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n\x00\xff"


@respx.mock
def test_fetch_ai_system_passport_pdf_returns_exact_bytes_unauthenticated():
    route = respx.get(
        f"{BASE_URL}/trust/passport/ai-systems/sys-1/passport.pdf"
    ).mock(
        return_value=httpx.Response(
            200, content=PDF_BYTES, headers={"content-type": "application/pdf"}
        )
    )
    pdf = _client().trust.fetch_ai_system_passport_pdf("sys-1")
    assert pdf == PDF_BYTES
    assert isinstance(pdf, bytes)
    assert "Authorization" not in route.calls.last.request.headers


@respx.mock
def test_fetch_ai_system_passport_pdf_maps_json_404_to_typed_error():
    # be's http-exception.filter envelope for an unknown AI System.
    respx.get(f"{BASE_URL}/trust/passport/ai-systems/nope/passport.pdf").mock(
        return_value=httpx.Response(
            404,
            json={
                "statusCode": 404,
                "path": "/trust/passport/ai-systems/nope/passport.pdf",
                "method": "GET",
                "requestId": "req-404",
                "message": "AI System not found",
            },
        )
    )
    with pytest.raises(NotFoundError) as exc_info:
        _client().trust.fetch_ai_system_passport_pdf("nope")
    assert exc_info.value.status_code == 404
    assert exc_info.value.request_id == "req-404"
    assert exc_info.value.body["message"] == "AI System not found"
    assert exc_info.value.retryable is False


# ── SDK-0310: AI System trust passport JSON + badge routes (BE-0540) ──────────

AI_SYSTEM_PASSPORT = {
    "@context": ["https://www.w3.org/2018/credentials/v1"],
    "type": ["VerifiableCredential", "AiSystemTrustPassport"],
    "id": "https://api.praesidia.ai/trust/passport/ai-systems/sys-1#2026-09-22T00:00:00.000Z",
    "issuer": "did:web:praesidia.ai:orgs:org-1",
    "issuanceDate": "2026-09-22T00:00:00.000Z",
    "expirationDate": "2026-09-23T00:00:00.000Z",
    "credentialSubject": {
        "id": "did:web:praesidia.ai:ai-systems:sys-1",
        "aiSystemName": "Fraud Triage",
        "posture": {"available": True, "counts": {"verified": 2}, "updatedAt": None},
        "redTeam": {"available": False, "reason": "AISYS-0031"},
        "attestations": {
            "activeCount": 2,
            "identityVerified": True,
            "guardrailsActive": True,
            "auditTrailEnabled": True,
            "spendCapConfigured": False,
        },
        "frameworks": ["EU-AI-Act"],
        "regulatoryClassification": {"available": True, "counts": {"high": 1}},
        "aibom": {"available": True, "digest": "sha256:abc", "version": 3},
        "dataCategories": {"available": True, "counts": {}},
        "incidents": {"available": True, "counts": {"open": 0}},
        "models": {"available": True, "counts": {"openai": 1}},
        "permissions": {"available": False, "reason": "AISYS-0040"},
        "evidenceRoot": {"available": False, "reason": "AISYS-0041"},
    },
    "proof": {
        "type": "Ed25519Signature2020",
        "created": "2026-09-22T00:00:00.000Z",
        "proofPurpose": "assertionMethod",
        "verificationMethod": "did:web:praesidia.ai:orgs:org-1#key-1",
        "keyVersion": 1,
        "proofValue": "c2ln",
    },
}
AI_SYSTEM_ROUTE = f"{BASE_URL}/trust/passport/ai-systems"


@respx.mock
def test_fetch_ai_system_passport_returns_passport_unauthenticated():
    route = respx.get(f"{AI_SYSTEM_ROUTE}/sys%201").mock(
        return_value=httpx.Response(200, json=AI_SYSTEM_PASSPORT)
    )
    passport = _client().trust.fetch_ai_system_passport("sys 1")
    assert passport == AI_SYSTEM_PASSPORT
    assert passport["credentialSubject"]["aibom"]["digest"] == "sha256:abc"
    assert "Authorization" not in route.calls.last.request.headers


@respx.mock
def test_fetch_ai_system_verify_bundle_returns_bundle_unauthenticated():
    bundle = {
        "passport": AI_SYSTEM_PASSPORT,
        "publicKeyJwk": {"kty": "OKP", "crv": "Ed25519", "x": "AAAA"},
        "verificationHint": "Import publicKeyJwk as an OKP Ed25519 key (alg: EdDSA).",
        "embed": {
            "badgeUrl": f"{AI_SYSTEM_ROUTE}/sys-1/badge.svg",
            "verifyUrl": f"{AI_SYSTEM_ROUTE}/sys-1/verify",
            "html": '<a href="…"><img src="…" /></a>',
            "markdown": "[![…](…)](…)",
        },
    }
    route = respx.get(f"{AI_SYSTEM_ROUTE}/sys-1/verify").mock(
        return_value=httpx.Response(200, json=bundle)
    )
    assert _client().trust.fetch_ai_system_verify_bundle("sys-1") == bundle
    assert "Authorization" not in route.calls.last.request.headers


@respx.mock
def test_fetch_ai_system_badge_svg_returns_svg_text_unauthenticated():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="20">'
        "<text>EU AI Act · high</text></svg>"
    )
    route = respx.get(f"{AI_SYSTEM_ROUTE}/sys-1/badge.svg").mock(
        return_value=httpx.Response(
            200,
            content=svg.encode("utf-8"),
            headers={"content-type": "image/svg+xml; charset=utf-8"},
        )
    )
    badge = _client().trust.fetch_ai_system_badge_svg("sys-1")
    assert badge == svg
    assert isinstance(badge, str)
    assert "Authorization" not in route.calls.last.request.headers


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        ("fetch_ai_system_passport", ""),
        ("fetch_ai_system_verify_bundle", "/verify"),
        ("fetch_ai_system_badge_svg", "/badge.svg"),
    ],
)
@respx.mock
def test_fetch_ai_system_routes_map_404_to_typed_error(method, suffix):
    # be's http-exception.filter envelope; message from AiSystemTrustPassportService.
    respx.get(f"{AI_SYSTEM_ROUTE}/nope{suffix}").mock(
        return_value=httpx.Response(
            404,
            json={
                "statusCode": 404,
                "path": f"/trust/passport/ai-systems/nope{suffix}",
                "method": "GET",
                "requestId": "req-404",
                "message": "Trust passport not found",
            },
        )
    )
    with pytest.raises(NotFoundError) as exc_info:
        getattr(_client().trust, method)("nope")
    assert exc_info.value.status_code == 404
    assert exc_info.value.request_id == "req-404"
    assert exc_info.value.body["message"] == "Trust passport not found"
    assert exc_info.value.retryable is False


@respx.mock
def test_fetch_ai_system_verify_bundle_maps_503_to_retryable_server_error():
    # be answers 503 when the org signing-key lookup fails (AUD-0027).
    respx.get(f"{AI_SYSTEM_ROUTE}/sys-1/verify").mock(
        return_value=httpx.Response(
            503, json={"statusCode": 503, "message": "Service Unavailable"}
        )
    )
    client = Praesidia(api_key="sk-test", org_id=ORG_ID, base_url=BASE_URL, retry=False)
    with pytest.raises(ServerError) as exc_info:
        client.trust.fetch_ai_system_verify_bundle("sys-1")
    assert exc_info.value.status_code == 503
    assert exc_info.value.retryable is True


# ── SDK-0309: OFFLINE verification of AI System passports (BE-0540) ─────────
# be-shaped fixture: AiSystemTrustPassportService.getPassport's unsigned doc
# signed through TrustPassportService.signCredentialDocument —
# canonicalJson(unsigned) under the org key, proof.created = issuanceDate,
# verificationMethod = f"{issuer}#key-{keyVersion}". Minted by Node's crypto
# over the TS SDK's canonicalJson (Ed25519, and low-s ES256 like the KMS path).

AI_SYSTEM_UNSIGNED = {
    "@context": [
        "https://www.w3.org/2018/credentials/v1",
        "https://praesidia.ai/credentials/trust-passport/v1",
    ],
    "type": ["VerifiableCredential", "AiSystemTrustPassport"],
    "id": "https://api.praesidia.ai/trust/passport/ai-systems/sys-1#2026-09-22T10:00:00.000Z",
    "issuer": "did:web:praesidia.ai:orgs:org-1",
    "issuanceDate": "2026-09-22T10:00:00.000Z",
    "expirationDate": "2999-09-23T10:00:00.000Z",
    "credentialSubject": {
        "id": "did:web:praesidia.ai:ai-systems:sys-1",
        "aiSystemName": "Fraud Triage",
        "posture": {
            "available": True,
            "counts": {"total": 2, "verified": 1, "unverified": 1},
            "updatedAt": "2026-10-01T00:00:00.000Z",
        },
        "redTeam": {"available": True, "counts": {"completedRuns": 3}, "updatedAt": None},
        "attestations": {
            "activeCount": 2,
            "identityVerified": True,
            "guardrailsActive": True,
            "auditTrailEnabled": True,
            "spendCapConfigured": False,
        },
        "frameworks": ["EU-AI-Act", "GDPR"],
        "regulatoryClassification": {
            "available": True,
            "counts": {"total": 1, "HIGH": 1},
            "updatedAt": "2026-09-20T08:00:00.000Z",
        },
        "aibom": {
            "available": True,
            "counts": {"componentCount": 14},
            "updatedAt": "2026-09-21T08:00:00.000Z",
            "digest": "sha256:9f2c",
            "version": 3,
        },
        "dataCategories": {"available": True, "counts": {"total": 4}},
        "incidents": {"available": True, "counts": {"total": 0}},
        "models": {"available": True, "counts": {"total": 1, "openai": 1}},
        "permissions": {"available": False, "reason": "AISYS-0031"},
        "evidenceRoot": {"available": False, "reason": "AISYS-0031"},
    },
}
AI_SYSTEM_SIGNED = {
    "Ed25519": (
        {
            "kty": "OKP",
            "crv": "Ed25519",
            "x": "y_iuLQGh_WfTz5nD-EZjr0wNjtcVP6q_0AJmYznNYjA",
        },
        "Ed25519Signature2020",
        "o0KyXR9wX2PyKv9xyzFwjbYp/MZdUkI4AaFEPTHyYVu/GCl0p7gVI/iMThmFZaX0fpFOBNy6/k5+Y5/hkCrwAg==",
    ),
    "ES256": (
        {
            "kty": "EC",
            "crv": "P-256",
            "x": "VpWwd17Ezos3PfkEnJpS-un06chVUz4PSx0KIftQ3bc",
            "y": "dxGtPi3JbL7qiConuR93Ur01_2nB_wuu0ij4WD-Ha1g",
            "use": "sig",
            "alg": "ES256",
        },
        "EcdsaSecp256r1Signature2019",
        "MEQCIHKJrdMjPmD5S6VQU/fHvNZxbWy0z6EtpvNthzuEfBYZAiBvkCyl7MP6xJymxPjSePe86XdaUdkMfB0DYnt4WRHKWA==",
    ),
}


def _signed_ai_system(algorithm):
    jwk, proof_type, proof_value = AI_SYSTEM_SIGNED[algorithm]
    passport = copy.deepcopy(AI_SYSTEM_UNSIGNED)
    passport["proof"] = {
        "type": proof_type,
        "created": passport["issuanceDate"],
        "proofPurpose": "assertionMethod",
        "verificationMethod": f"{passport['issuer']}#key-2",
        "keyVersion": 2,
        "proofValue": proof_value,
    }
    return passport, jwk


def _mock_ai_system_bundle(passport, public_key_jwk):
    route = respx.get(f"{AI_SYSTEM_ROUTE}/sys-1/verify").mock(
        return_value=httpx.Response(
            200,
            json={
                "passport": passport,
                "publicKeyJwk": public_key_jwk,
                "verificationHint": "Import publicKeyJwk…",
                "embed": {"badgeUrl": "b", "verifyUrl": "v", "html": "h", "markdown": "m"},
            },
        )
    )
    return route, _client().trust


@pytest.mark.parametrize("algorithm", ["Ed25519", "ES256"])
def test_verify_ai_system_passport_accepts_be_signed_passport_under_pinned_key(algorithm):
    passport, jwk = _signed_ai_system(algorithm)
    assert verify_ai_system_passport(passport, jwk) == {
        "verified": True,
        "signatureValid": True,
        "expired": False,
        "reason": "ok",
    }
    assert _client().trust.verify_ai_system_passport(passport, jwk)["reason"] == "ok"


@respx.mock
@pytest.mark.parametrize("algorithm", ["Ed25519", "ES256"])
def test_fetch_and_verify_ai_system_with_trusted_key(algorithm):
    passport, jwk = _signed_ai_system(algorithm)
    route, trust = _mock_ai_system_bundle(passport, jwk)

    result = trust.fetch_and_verify_ai_system("sys-1", trusted_keys=[jwk])

    assert result["verified"] is True and result["reason"] == "ok"
    assert result["passport"]["credentialSubject"]["aiSystemName"] == "Fraud Triage"
    assert result["publicKeyJwk"] == jwk
    assert "authorization" not in route.calls.last.request.headers


@respx.mock
def test_fetch_and_verify_ai_system_without_anchor_is_unpinned():
    passport, jwk = _signed_ai_system("Ed25519")
    _, trust = _mock_ai_system_bundle(passport, jwk)

    result = trust.fetch_and_verify_ai_system("sys-1")

    assert result["verified"] is False
    assert result["reason"] == "unpinned_key"
    assert result["signatureValid"] is True


@respx.mock
def test_fetch_and_verify_ai_system_fingerprint_pin_and_foreign_anchor():
    passport, jwk = _signed_ai_system("ES256")
    _, trust = _mock_ai_system_bundle(passport, jwk)
    assert (
        trust.fetch_and_verify_ai_system(
            "sys-1", expected_fingerprint=jwk_thumbprint(jwk)
        )["reason"]
        == "ok"
    )
    foreign = AI_SYSTEM_SIGNED["Ed25519"][0]
    assert (
        trust.fetch_and_verify_ai_system("sys-1", trusted_keys=[foreign])["reason"]
        == "untrusted_key"
    )


@respx.mock
@pytest.mark.parametrize("algorithm", ["Ed25519", "ES256"])
def test_tampered_ai_system_subject_is_signature_mismatch(algorithm):
    passport, jwk = _signed_ai_system(algorithm)
    passport["credentialSubject"]["incidents"]["counts"]["total"] = 7

    result = verify_ai_system_passport(passport, jwk)
    assert result["reason"] == "signature-mismatch"
    assert result["signatureValid"] is False and result["verified"] is False

    _, trust = _mock_ai_system_bundle(passport, jwk)
    fetched = trust.fetch_and_verify_ai_system("sys-1", trusted_keys=[jwk])
    assert fetched["verified"] is False and fetched["signatureValid"] is False


def test_agent_and_ai_system_envelopes_do_not_cross_verify():
    passport, jwk = _signed_ai_system("Ed25519")
    assert verify_passport(passport, jwk)["reason"] == "malformed-passport"
    assert (
        verify_ai_system_passport(PASSPORT, PUBLIC_KEY_JWK)["reason"]
        == "malformed-passport"
    )


@pytest.mark.parametrize(
    "section, value",
    [
        ("permissions", {"available": False, "reason": "AISYS-0031", "counts": {"total": 0}}),
        ("evidenceRoot", {"available": False}),
        ("incidents", {"available": True, "counts": {"total": 0.5}}),
        ("models", None),
        ("posture", {"available": True, "updatedAt": "2026-10-01"}),
    ],
)
def test_verify_ai_system_passport_rejects_malformed_subject(section, value):
    # The envelope check runs before the signature, so these are rejected
    # as malformed regardless of the (now stale) proof.
    passport, jwk = _signed_ai_system("Ed25519")
    passport["credentialSubject"][section] = value
    assert verify_ai_system_passport(passport, jwk)["reason"] == "malformed-passport"


# ── SDK-0311: PraesidiaTrust — the trust routes with no Praesidia account ────


@pytest.mark.parametrize(
    ("method", "arg", "path", "response"),
    [
        ("fetch_passport", "agent-1", "/agent-1", httpx.Response(200, json=PASSPORT)),
        ("fetch_verify_bundle", "agent-1", "/agent-1/verify", httpx.Response(200, json={})),
        ("fetch_ai_system_passport", "sys-1", "/ai-systems/sys-1", httpx.Response(200, json={})),
        ("fetch_ai_system_verify_bundle", "sys-1", "/ai-systems/sys-1/verify", httpx.Response(200, json={})),
        ("fetch_ai_system_badge_svg", "sys-1", "/ai-systems/sys-1/badge.svg", httpx.Response(200, text="<svg/>")),
        ("fetch_ai_system_passport_pdf", "sys-1", "/ai-systems/sys-1/passport.pdf", httpx.Response(200, content=PDF_BYTES)),
    ],
)
@respx.mock
def test_praesidia_trust_calls_every_public_route_without_credentials(method, arg, path, response):
    route = respx.get(f"{BASE_URL}/trust/passport{path}").mock(return_value=response)
    getattr(PraesidiaTrust(base_url=BASE_URL), method)(arg)  # no api_key, no org_id
    assert route.called
    assert "authorization" not in route.calls.last.request.headers


@respx.mock
def test_praesidia_trust_fetch_and_verify_ai_system_needs_no_account():
    passport, jwk = _signed_ai_system("Ed25519")
    route, _ = _mock_ai_system_bundle(passport, jwk)
    result = PraesidiaTrust(base_url=BASE_URL).fetch_and_verify_ai_system(
        "sys-1", trusted_keys=[jwk]
    )
    assert result["verified"] is True and result["reason"] == "ok"
    assert "authorization" not in route.calls.last.request.headers


@respx.mock
def test_praesidia_trust_base_url_defaults_like_the_ts_sdk(monkeypatch):
    monkeypatch.delenv("PRAESIDIA_BASE_URL", raising=False)
    prod = respx.get("https://api.praesidia.ai/trust/passport/agent-1").mock(
        return_value=httpx.Response(200, json=PASSPORT)
    )
    PraesidiaTrust().fetch_passport("agent-1")
    assert prod.called
    monkeypatch.setenv("PRAESIDIA_BASE_URL", BASE_URL)
    local = respx.get(f"{BASE_URL}/trust/passport/agent-1").mock(
        return_value=httpx.Response(200, json=PASSPORT)
    )
    PraesidiaTrust().fetch_passport("agent-1")
    assert local.called


def test_praesidia_trust_is_additive_and_validates_its_config():
    assert isinstance(PraesidiaTrust(), TrustResource)
    assert isinstance(_client().trust, TrustResource)
    with pytest.raises(ValueError):
        PraesidiaTrust(base_url="ftp://test.local")
    with pytest.raises(ValueError):
        PraesidiaTrust(timeout=0)
    # The authenticated client still requires a real credential.
    with pytest.raises(ValueError, match="api_key"):
        Praesidia(api_key=None, org_id=ORG_ID, base_url=BASE_URL)  # type: ignore[arg-type]
