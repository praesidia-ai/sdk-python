"""
Praesidia SDK — TrustResource (H3-02f): fetch + OFFLINE-verify a peer agent's
trust passport.

This is the "verify a peer agent's reputation WITHOUT trusting Praesidia"
client. It fetches the signed, W3C-Verifiable-Credential-shaped trust passport
from the PUBLIC (unauthenticated) trust routes and verifies the detached Ed25519
proof LOCALLY against the org public key JWK — the same offline-verify pattern as
the ``@praesidia/audit-verifier`` package.

The Ed25519 verify + canonical-JSON primitives live in ``_crypto.py``
(hand-written, pure-Python, zero dependencies). :func:`verify_passport` is a
module-level function so the offline check can be used WITHOUT a client / account
(e.g. verifying a passport handed to you out-of-band).
"""

from __future__ import annotations

import base64
from typing import Any, Optional

from ._crypto import (
    canonical_json,
    ed25519_public_key_from_jwk,
    ed25519_verify,
)
from ._http import HttpClient

# ── Standalone offline verification (no client / account required) ──────────


def verify_passport(
    passport: dict[str, Any],
    public_key_jwk: dict[str, Any],
) -> dict[str, Any]:
    """
    OFFLINE-verify a passport's detached Ed25519 proof against a public key JWK.

    Reconstructs the canonical JSON of the passport WITH its ``proof`` member
    removed (RFC-8785-style, keys sorted lexicographically — byte-identical to how
    be-core signed it), base64-decodes ``proof.proofValue``, and verifies the
    EdDSA signature directly over those raw bytes. Also checks ``expirationDate``.

    Pure local computation — no network, no trust in Praesidia. Never raises; a
    malformed passport / key yields ``{"verified": False, "reason": ...}``.

    Returns a dict::

        {
          "verified": bool,        # signature valid AND not expired
          "signatureValid": bool,  # signature valid (ignores expiry)
          "expired": bool,
          "reason": "ok" | "missing-proof" | "malformed-public-key"
                    | "signature-mismatch" | "expired",
        }
    """
    proof = passport.get("proof") if isinstance(passport, dict) else None
    if not isinstance(proof, dict) or not isinstance(
        proof.get("proofValue"), str
    ):
        return _result(False, False, False, "missing-proof")

    public_key = ed25519_public_key_from_jwk(public_key_jwk)
    if public_key is None:
        return _result(False, False, False, "malformed-public-key")

    # Sign-the-doc / attach-the-proof: strip `proof`, canonicalize the rest.
    unsigned = {k: v for k, v in passport.items() if k != "proof"}
    message = canonical_json(unsigned)
    try:
        signature = base64.b64decode(proof["proofValue"])
    except Exception:
        return _result(False, False, False, "signature-mismatch")

    signature_valid = ed25519_verify(message, signature, public_key)
    expired = _is_expired(passport.get("expirationDate"))

    if not signature_valid:
        return _result(False, False, expired, "signature-mismatch")
    if expired:
        return _result(False, True, True, "expired")
    return _result(True, True, False, "ok")


def _result(
    verified: bool, signature_valid: bool, expired: bool, reason: str
) -> dict[str, Any]:
    return {
        "verified": verified,
        "signatureValid": signature_valid,
        "expired": expired,
        "reason": reason,
    }


def _is_expired(expiration_date: Optional[str]) -> bool:
    if not expiration_date:
        return False
    from datetime import datetime, timezone

    try:
        text = expiration_date.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt < datetime.now(timezone.utc)
    except Exception:
        return False


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
        return self._http.get(f"/trust/passport/{agent_id}")

    def fetch_verify_bundle(self, agent_id: str) -> dict[str, Any]:
        """
        Fetch the verification bundle (passport + org public key JWK +
        didDocumentUrl + hint). ``GET /trust/passport/{agent_id}/verify`` (public).
        """
        return self._http.get(f"/trust/passport/{agent_id}/verify")

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
