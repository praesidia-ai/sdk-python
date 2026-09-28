"""SDK-0366 (TS twin SDK-0363) / ADR-0004 / AV-0018 contract: a trust passport
with ``proof.signatureFormat == 2`` is signed over
``ASCII("praesidia:" + purpose + ":v2\\n") || canonical(passport without proof)``.
Mirrors ``sdk/src/trust-signature-format.spec.ts``.

The signers below are test-only (RFC 8032 Ed25519; ECDSA P-256 with a
deterministic nonce and low-S), because ``cryptography`` is not a dev
dependency. Cross-language byte agreement is covered by the Node-minted
fixtures in ``test_trust.py``.
"""

from __future__ import annotations

import base64
import copy
import hashlib

import httpx
import pytest
import respx

from praesidia import Praesidia, verify_ai_system_passport, verify_passport
from praesidia._crypto import (
    _B,
    _L,
    _P256_G,
    _P256_HALF_N,
    _P256_N,
    _encode_point,
    _p256_scalarmult,
    _scalarmult,
    canonical_json,
)

ISSUED = "2026-09-28T00:00:00.000Z"
ISSUER = "did:web:praesidia.ai:orgs:org-1"
ATTESTATIONS = {
    "activeCount": 1,
    "identityVerified": True,
    "guardrailsActive": True,
    "auditTrailEnabled": True,
    "spendCapConfigured": False,
}


def _envelope(vc_type):
    return {
        "@context": ["https://www.w3.org/2018/credentials/v1"],
        "type": ["VerifiableCredential", vc_type],
        "id": f"https://api.praesidia.ai/trust/passport/x#{ISSUED}",
        "issuer": ISSUER,
        "issuanceDate": ISSUED,
        "expirationDate": "2999-01-01T00:00:00.000Z",
    }


def _agent_unsigned():
    return {
        **_envelope("TrustPassport"),
        "credentialSubject": {
            "id": "did:web:praesidia.ai:agents:a-1",
            "agentName": "Nova",
            "trustLevel": "TRUSTED",
            "trustScore": 80,
            "posture": {"status": "verified", "expiresAt": None},
            "redTeam": {"completedRuns": 0, "lastTestedAt": None},
            "attestations": dict(ATTESTATIONS),
            "compliance": ["GDPR"],
        },
    }


def _ai_system_unsigned():
    on = {"available": True}
    sections = (
        "posture", "redTeam", "regulatoryClassification", "aibom",
        "dataCategories", "incidents", "models", "evidenceRoot",
    )
    subject = {
        "id": "did:web:praesidia.ai:ai-systems:s-1",
        "aiSystemName": "Fraud Triage",
        "attestations": dict(ATTESTATIONS),
        "frameworks": ["GDPR"],
        "permissions": {"available": False, "reason": "n/a"},
        **{name: dict(on) for name in sections},
    }
    return {**_envelope("AiSystemTrustPassport"), "credentialSubject": subject}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _ed25519_sign(message: bytes):
    h = hashlib.sha512(b"\x01" * 32).digest()
    a = int.from_bytes(h[:32], "little")
    a = (a & ((1 << 254) - 8)) | (1 << 254)
    public = _encode_point(_scalarmult(_B, a))
    r = int.from_bytes(hashlib.sha512(h[32:] + message).digest(), "little") % _L
    big_r = _encode_point(_scalarmult(_B, r))
    k = int.from_bytes(hashlib.sha512(big_r + public + message).digest(), "little") % _L
    signature = big_r + ((r + k * a) % _L).to_bytes(32, "little")
    return signature, {"kty": "OKP", "crv": "Ed25519", "x": _b64url(public)}


def _der_int(value: int) -> bytes:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    if raw[0] & 0x80:
        raw = b"\x00" + raw
    return b"\x02" + bytes([len(raw)]) + raw


def _es256_sign(message: bytes):
    d = 0x1234567890ABCDEF % _P256_N
    qx, qy = _p256_scalarmult(_P256_G, d)
    z = int.from_bytes(hashlib.sha256(message).digest(), "big")
    k = int.from_bytes(hashlib.sha256(d.to_bytes(32, "big") + message).digest(), "big") % _P256_N
    r = _p256_scalarmult(_P256_G, k)[0] % _P256_N
    s = pow(k, -1, _P256_N) * (z + r * d) % _P256_N
    if s > _P256_HALF_N:
        s = _P256_N - s
    body = _der_int(r) + _der_int(s)
    jwk = {
        "kty": "EC",
        "crv": "P-256",
        "x": _b64url(qx.to_bytes(32, "big")),
        "y": _b64url(qy.to_bytes(32, "big")),
    }
    return b"\x30" + bytes([len(body)]) + body, jwk


_ABSENT = object()


def _sign(unsigned, algorithm, fmt=_ABSENT, purpose=None):
    """Sign like be: format 1 = canonical bytes; format 2 = purpose tag || canonical."""
    message = canonical_json(unsigned)
    if purpose:
        message = f"praesidia:{purpose}:v2\n".encode("ascii") + message
    signer = _ed25519_sign if algorithm == "Ed25519" else _es256_sign
    signature, jwk = signer(message)
    proof = {
        "type": "Ed25519Signature2020" if algorithm == "Ed25519" else "EcdsaSecp256r1Signature2019",
        "created": ISSUED,
        "proofPurpose": "assertionMethod",
        "verificationMethod": f"{ISSUER}#key-1",
        "keyVersion": 1,
        "proofValue": base64.b64encode(signature).decode("ascii"),
    }
    if fmt is not _ABSENT:
        proof["signatureFormat"] = fmt
    return {**unsigned, "proof": proof}, jwk


def _relabel(passport, fmt):
    relabelled = copy.deepcopy(passport)
    if fmt is _ABSENT:
        relabelled["proof"].pop("signatureFormat", None)
    else:
        relabelled["proof"]["signatureFormat"] = fmt
    return relabelled


KINDS = pytest.mark.parametrize(
    "build,verify",
    [(_agent_unsigned, verify_passport), (_ai_system_unsigned, verify_ai_system_passport)],
    ids=["agent", "ai-system"],
)
ALGORITHMS = pytest.mark.parametrize("algorithm", ["Ed25519", "ES256"])
OK = {"verified": True, "signatureValid": True, "expired": False, "reason": "ok"}


@KINDS
@ALGORITHMS
def test_format_1_absent_verifies_unchanged(build, verify, algorithm):
    passport, jwk = _sign(build(), algorithm)
    assert verify(passport, jwk) == OK


@KINDS
@ALGORITHMS
def test_explicit_format_1_verifies_over_untagged_bytes(build, verify, algorithm):
    passport, jwk = _sign(build(), algorithm, 1)
    assert verify(passport, jwk) == OK


@KINDS
@ALGORITHMS
def test_format_2_with_trust_passport_purpose_verifies(build, verify, algorithm):
    passport, jwk = _sign(build(), algorithm, 2, "trust-passport")
    assert verify(passport, jwk) == OK


@KINDS
@ALGORITHMS
def test_format_2_signed_for_governance_badge_is_signature_mismatch(build, verify, algorithm):
    passport, jwk = _sign(build(), algorithm, 2, "governance-badge")
    assert verify(passport, jwk) == {
        "verified": False,
        "signatureValid": False,
        "expired": False,
        "reason": "signature-mismatch",
    }


@KINDS
@ALGORITHMS
def test_format_2_signature_relabelled_as_format_1_or_unlabelled_fails(build, verify, algorithm):
    passport, jwk = _sign(build(), algorithm, 2, "trust-passport")
    for fmt in (1, _ABSENT):
        assert verify(_relabel(passport, fmt), jwk)["reason"] == "signature-mismatch"


@KINDS
@ALGORITHMS
def test_format_1_signature_relabelled_as_format_2_fails(build, verify, algorithm):
    passport, jwk = _sign(build(), algorithm, 1)
    assert verify(_relabel(passport, 2), jwk)["reason"] == "signature-mismatch"


@KINDS
@ALGORITHMS
@pytest.mark.parametrize("fmt", [None, "2", 3, True, False, 2.0])
def test_other_signature_formats_fail_closed_as_malformed(build, verify, algorithm, fmt):
    # True == 1 and 2.0 == 2 in Python: only a JSON integer 1 or 2 is a format.
    passport, jwk = _sign(build(), algorithm, fmt, "trust-passport")
    assert verify(passport, jwk) == {
        "verified": False,
        "signatureValid": False,
        "expired": False,
        "reason": "malformed-passport",
    }


@respx.mock
@pytest.mark.parametrize("purpose,verified", [("trust-passport", True), ("governance-badge", False)])
def test_fetch_and_verify_format_2_under_a_pinned_key(purpose, verified):
    passport, jwk = _sign(_agent_unsigned(), "Ed25519", 2, purpose)
    respx.get("https://test.local/trust/passport/a-1/verify").mock(
        return_value=httpx.Response(
            200, json={"passport": passport, "publicKeyJwk": jwk, "verificationHint": "h"}
        )
    )
    client = Praesidia(api_key="sk-test", org_id="org-1", base_url="https://test.local")
    result = client.trust.fetch_and_verify("a-1", trusted_keys=[jwk])
    assert result["verified"] is verified
