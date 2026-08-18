"""
Praesidia SDK — TrustResource (H3-02f): fetch + OFFLINE-verify a peer agent's
trust passport.

This local cryptographic verification client fetches a signed,
W3C-Verifiable-Credential-shaped trust passport from the PUBLIC
(unauthenticated) routes and verifies the detached Ed25519 or KMS-backed
P-256/ES256 proof against a caller-trusted public key JWK.

The Ed25519 verify + canonical-JSON primitives live in ``_crypto.py``
(hand-written, pure-Python, zero dependencies). :func:`verify_passport` is a
module-level function so the offline check can be used WITHOUT a client / account
(e.g. verifying a passport handed to you out-of-band).
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from typing import Any, Optional

from ._crypto import (
    canonical_json,
    ed25519_public_key_from_jwk,
    ed25519_verify,
    es256_verify,
    p256_public_key_from_jwk,
)
from ._http import HttpClient, path_segment

# ── Standalone offline verification (no client / account required) ──────────

_STANDARD_BASE64_RE = re.compile(
    r"^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$"
)
_CANONICAL_INSTANT_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)


def verify_passport(
    passport: dict[str, Any],
    public_key_jwk: dict[str, Any],
) -> dict[str, Any]:
    """
    OFFLINE-verify a passport's detached Ed25519/ES256 proof against a JWK.

    Reconstructs the canonical JSON of the passport WITH its ``proof`` member
    removed (RFC-8785-style, keys sorted lexicographically — byte-identical to how
    be-core signed it), base64-decodes ``proof.proofValue``, and verifies the
    substrate-selected signature over those bytes. Also checks expiration.

    Pure local computation against the supplied trust anchor. Never raises; a
    malformed passport / key yields ``{"verified": False, "reason": ...}``.

    Returns a dict::

        {
          "verified": bool,        # signature valid AND not expired
          "signatureValid": bool,  # signature valid (ignores expiry)
          "expired": bool,
          "reason": "ok" | "missing-proof" | "malformed-public-key"
                    | "signature-mismatch" | "invalid-expiration"
                    | "malformed-passport" | "expired",
        }
    """
    try:
        return _verify_passport_unchecked(passport, public_key_jwk)
    except Exception:
        return _result(False, False, False, "malformed-passport")


def _verify_passport_unchecked(
    passport: dict[str, Any],
    public_key_jwk: dict[str, Any],
) -> dict[str, Any]:
    proof = passport.get("proof") if isinstance(passport, dict) else None
    if not isinstance(proof, dict) or not isinstance(
        proof.get("proofValue"), str
    ):
        return _result(False, False, False, "missing-proof")
    if not _passport_envelope_well_formed(passport):
        return _result(False, False, False, "malformed-passport")

    algorithm = None
    public_key: Any = None
    if (
        isinstance(public_key_jwk, dict)
        and public_key_jwk.get("kty") == "OKP"
        and public_key_jwk.get("crv") == "Ed25519"
    ):
        algorithm = "Ed25519"
        public_key = ed25519_public_key_from_jwk(public_key_jwk)
    elif (
        isinstance(public_key_jwk, dict)
        and public_key_jwk.get("kty") == "EC"
        and public_key_jwk.get("crv") == "P-256"
    ):
        algorithm = "ES256"
        public_key = p256_public_key_from_jwk(public_key_jwk)
    if public_key is None or algorithm is None:
        return _result(False, False, False, "malformed-public-key")

    # Sign-the-doc / attach-the-proof: strip `proof`, canonicalize the rest.
    try:
        unsigned = {k: v for k, v in passport.items() if k != "proof"}
        message = canonical_json(unsigned)
    except (AttributeError, TypeError, UnicodeError, ValueError):
        return _result(False, False, False, "malformed-passport")
    try:
        signature = _decode_standard_base64(proof["proofValue"])
    except (ValueError, TypeError):
        return _result(False, False, False, "signature-mismatch")

    if algorithm == "Ed25519":
        signature_valid = (
            proof.get("type") == "Ed25519Signature2020"
            and ed25519_verify(message, signature, public_key)
        )
    else:
        signature_valid = (
            proof.get("type") == "EcdsaSecp256r1Signature2019"
            and es256_verify(message, signature, public_key)
        )
    expiration = _expiration_state(
        passport.get("expirationDate"), passport["issuanceDate"]
    )
    expired = expiration == "expired"

    if not signature_valid:
        return _result(False, False, expired, "signature-mismatch")
    if expiration == "invalid":
        return _result(False, True, False, "invalid-expiration")
    if expired:
        return _result(False, True, True, "expired")
    return _result(True, True, False, "ok")


def _passport_envelope_well_formed(passport: dict[str, Any]) -> bool:
    contexts = passport.get("@context")
    types = passport.get("type")
    subject = passport.get("credentialSubject")
    proof = passport.get("proof")
    if not isinstance(subject, dict) or not isinstance(proof, dict):
        return False
    posture = subject.get("posture")
    red_team = subject.get("redTeam")
    attestations = subject.get("attestations")
    compliance = subject.get("compliance")
    key_version = proof.get("keyVersion")
    return (
        isinstance(contexts, list)
        and "https://www.w3.org/2018/credentials/v1" in contexts
        and isinstance(types, list)
        and "VerifiableCredential" in types
        and "TrustPassport" in types
        and _nonempty_string(passport.get("id"))
        and _nonempty_string(passport.get("issuer"))
        and _parse_canonical_instant(passport.get("issuanceDate")) is not None
        and _nonempty_string(subject.get("id"))
        and _nonempty_string(subject.get("agentName"))
        and _nonempty_string(subject.get("trustLevel"))
        and _finite_number_in_range(subject.get("trustScore"), 0, 100)
        and isinstance(compliance, list)
        and all(_nonempty_string(item) for item in compliance)
        and isinstance(posture, dict)
        and _nonempty_string(posture.get("status"))
        and _nullable_canonical_instant(posture.get("expiresAt"))
        and isinstance(red_team, dict)
        and _nonnegative_integer(red_team.get("completedRuns"))
        and _nullable_canonical_instant(red_team.get("lastTestedAt"))
        and isinstance(attestations, dict)
        and _nonnegative_integer(attestations.get("activeCount"))
        and all(
            isinstance(attestations.get(field), bool)
            for field in (
                "identityVerified",
                "guardrailsActive",
                "auditTrailEnabled",
                "spendCapConfigured",
            )
        )
        and _nonempty_string(proof.get("type"))
        and proof.get("created") == passport.get("issuanceDate")
        and proof.get("proofPurpose") == "assertionMethod"
        and _positive_integer(key_version)
        and proof.get("verificationMethod")
        == f"{passport.get('issuer')}#key-{key_version}"
    )


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _finite_number_in_range(value: Any, minimum: float, maximum: float) -> bool:
    import math

    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and minimum <= value <= maximum
    )


def _nonnegative_integer(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _positive_integer(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


def _nullable_canonical_instant(value: Any) -> bool:
    return value is None or _parse_canonical_instant(value) is not None


def _parse_canonical_instant(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not _CANONICAL_INSTANT_RE.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except (TypeError, ValueError, OverflowError):
        return None


def _decode_standard_base64(value: str) -> bytes:
    if (
        not value
        or len(value) > 96
        or len(value) % 4
        or not _STANDARD_BASE64_RE.fullmatch(value)
    ):
        raise ValueError("non-canonical base64")
    decoded = base64.b64decode(value, validate=True)
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError("non-canonical base64")
    return decoded


def _result(
    verified: bool, signature_valid: bool, expired: bool, reason: str
) -> dict[str, Any]:
    return {
        "verified": verified,
        "signatureValid": signature_valid,
        "expired": expired,
        "reason": reason,
    }


def _expiration_state(expiration_date: Any, issuance_date: str) -> str:
    expiration = _parse_canonical_instant(expiration_date)
    issuance = _parse_canonical_instant(issuance_date)
    if expiration is None or issuance is None or expiration <= issuance:
        return "invalid"
    return "expired" if expiration <= datetime.now(timezone.utc) else "valid"


class TrustResource:
    """
    Fetch + offline-verify an agent's trust passport (public routes).

    Example::

        result = client.trust.fetch_and_verify(peer_agent_id)
        if result["verified"] and result["passport"]["credentialSubject"]["trustScore"] >= 70:
            ...  # trust the peer
    """

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def fetch_passport(self, agent_id: str) -> dict[str, Any]:
        """
        Fetch an agent's signed trust passport. ``GET /trust/passport/{agent_id}``
        (public — no auth). Raises ``NotFoundError`` for unknown, inactive,
        soft-deleted, or non-PUBLIC agents.
        """
        return self._http.get(
            f"/trust/passport/{path_segment(agent_id, 'agent_id')}",
            include_auth=False,
        )

    def fetch_verify_bundle(self, agent_id: str) -> dict[str, Any]:
        """
        Fetch the verification bundle (passport + org public key JWK +
        didDocumentUrl + hint). ``GET /trust/passport/{agent_id}/verify`` (public).
        """
        return self._http.get(
            f"/trust/passport/{path_segment(agent_id, 'agent_id')}/verify",
            include_auth=False,
        )

    def verify_passport(
        self,
        passport: dict[str, Any],
        public_key_jwk: dict[str, Any],
    ) -> dict[str, Any]:
        """Offline-verify a passport (delegates to :func:`verify_passport`)."""
        return verify_passport(passport, public_key_jwk)

    def fetch_and_verify(self, agent_id: str) -> dict[str, Any]:
        """
        Fetch the verification bundle AND verify it offline in one call. Returns
        the verification result with ``passport``, ``publicKeyJwk`` and
        ``didDocumentUrl`` merged in.
        """
        bundle = self.fetch_verify_bundle(agent_id)
        result = verify_passport(bundle["passport"], bundle["publicKeyJwk"])
        result["passport"] = bundle["passport"]
        result["publicKeyJwk"] = bundle["publicKeyJwk"]
        result["didDocumentUrl"] = bundle.get("didDocumentUrl")
        return result
