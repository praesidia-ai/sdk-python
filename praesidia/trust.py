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

SEC-2026-09-12 MCPSDK-04: :meth:`TrustResource.fetch_and_verify` takes the
passport AND the key from the same unauthenticated GET, so it now requires a
caller-supplied trust anchor (``trusted_keys`` / ``expected_fingerprint``)
before it will report ``verified: True``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Sequence, Union

from ._crypto import (
    canonical_json,
    ed25519_public_key_from_jwk,
    ed25519_verify,
    es256_verify,
    p256_public_key_from_jwk,
)
from ._http import HttpClient, path_segment
from ._retry import RetryConfig

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
          "verified": bool,        # signature valid under a TRUSTED key
                                   # AND not expired
          "signatureValid": bool,  # signature valid under the key actually
                                   # used (ignores expiry)
          "expired": bool,
          "reason": "ok" | "missing-proof" | "malformed-public-key"
                    | "signature-mismatch" | "invalid-expiration"
                    | "malformed-passport" | "expired"
                    # trust-anchor outcomes from fetch_and_verify (MCPSDK-04):
                    | "unpinned_key" | "untrusted_key"
                    | "fingerprint_mismatch",
        }
    """
    return _verify_credential(passport, public_key_jwk, _passport_envelope_well_formed)


def verify_ai_system_passport(
    passport: dict[str, Any],
    public_key_jwk: dict[str, Any],
) -> dict[str, Any]:
    """
    OFFLINE-verify an AI System passport (BE-0540) against a JWK.

    Same proof, canonicalization, signature and expiry rules as
    :func:`verify_passport` (be signs both through one path); only the envelope
    check differs — ``type`` must include ``AiSystemTrustPassport`` and the AI
    System credential subject must match be's contract. An agent passport is
    ``malformed-passport`` here, and an AI System passport is
    ``malformed-passport`` in :func:`verify_passport`. Never raises; returns the
    same result dict as :func:`verify_passport`.
    """
    return _verify_credential(
        passport, public_key_jwk, _ai_system_passport_envelope_well_formed
    )


def _verify_credential(
    passport: dict[str, Any],
    public_key_jwk: dict[str, Any],
    envelope_well_formed: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    try:
        return _verify_passport_unchecked(
            passport, public_key_jwk, envelope_well_formed
        )
    except Exception:
        return _result(False, False, False, "malformed-passport")


def _verify_passport_unchecked(
    passport: dict[str, Any],
    public_key_jwk: dict[str, Any],
    envelope_well_formed: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    proof = passport.get("proof") if isinstance(passport, dict) else None
    if not isinstance(proof, dict) or not isinstance(
        proof.get("proofValue"), str
    ):
        return _result(False, False, False, "missing-proof")
    if not envelope_well_formed(passport):
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


def _credential_envelope_well_formed(
    passport: dict[str, Any], credential_type: str
) -> bool:
    """
    VC + proof envelope common to both passport kinds (be's
    ``signCredentialDocument``), plus the subject ``id`` and ``attestations``
    block they share. ``credential_type`` is the specific VC type that must be
    present (``TrustPassport`` / ``AiSystemTrustPassport``).
    """
    contexts = passport.get("@context")
    types = passport.get("type")
    subject = passport.get("credentialSubject")
    proof = passport.get("proof")
    if not isinstance(subject, dict) or not isinstance(proof, dict):
        return False
    attestations = subject.get("attestations")
    key_version = proof.get("keyVersion")
    return (
        isinstance(contexts, list)
        and "https://www.w3.org/2018/credentials/v1" in contexts
        and isinstance(types, list)
        and "VerifiableCredential" in types
        and credential_type in types
        and _nonempty_string(passport.get("id"))
        and _nonempty_string(passport.get("issuer"))
        and _parse_canonical_instant(passport.get("issuanceDate")) is not None
        and _nonempty_string(subject.get("id"))
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


def _passport_envelope_well_formed(passport: dict[str, Any]) -> bool:
    if not _credential_envelope_well_formed(passport, "TrustPassport"):
        return False
    subject = passport["credentialSubject"]
    posture = subject.get("posture")
    red_team = subject.get("redTeam")
    compliance = subject.get("compliance")
    return (
        _nonempty_string(subject.get("agentName"))
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
    )


_AI_SYSTEM_SECTIONS = (
    "posture",
    "redTeam",
    "regulatoryClassification",
    "aibom",
    "dataCategories",
    "incidents",
    "models",
    "permissions",
    "evidenceRoot",
)


def _ai_system_passport_envelope_well_formed(passport: dict[str, Any]) -> bool:
    if not _credential_envelope_well_formed(passport, "AiSystemTrustPassport"):
        return False
    subject = passport["credentialSubject"]
    frameworks = subject.get("frameworks")
    aibom = subject.get("aibom")
    return (
        _nonempty_string(subject.get("aiSystemName"))
        and isinstance(frameworks, list)
        and all(_nonempty_string(item) for item in frameworks)
        and all(
            _ai_system_section_well_formed(subject.get(name))
            for name in _AI_SYSTEM_SECTIONS
        )
        and ("digest" not in aibom or _nonempty_string(aibom["digest"]))
        and ("version" not in aibom or _nonnegative_integer(aibom["version"]))
    )


def _ai_system_section_well_formed(section: Any) -> bool:
    """
    be's section contract: ``{"available": True, "counts"?, "updatedAt"?}`` or
    ``{"available": False, "reason"}`` — ``reason`` only on a gap, and a gap
    never carries counts (never a fabricated zero).
    """
    if not isinstance(section, dict) or not isinstance(
        section.get("available"), bool
    ):
        return False
    available = section["available"]
    counts = section.get("counts")
    return (
        ("reason" not in section if available else _nonempty_string(section.get("reason")))
        and (
            "counts" not in section
            or (
                available
                and isinstance(counts, dict)
                and all(_nonnegative_integer(v) for v in counts.values())
            )
        )
        and (
            "updatedAt" not in section
            or _nullable_canonical_instant(section["updatedAt"])
        )
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

    The verify route is public and returns the passport AND the key, so
    ``fetch_and_verify`` needs an out-of-band trust anchor before it will report
    ``verified: True`` (SEC-2026-09-12 MCPSDK-04).

    Example::

        result = client.trust.fetch_and_verify(
            peer_agent_id, trusted_keys=[issuer_jwk_from_your_did_document]
        )
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

    def fetch_ai_system_passport(self, ai_system_id: str) -> dict[str, Any]:
        """
        Fetch an AI System's signed trust passport (BE-0540), aggregated over
        its member assets. ``GET /trust/passport/ai-systems/{ai_system_id}``
        (public — no auth). Shape: be's ``AiSystemTrustPassportDto`` — a W3C VC
        (``type: ["VerifiableCredential", "AiSystemTrustPassport"]``) whose
        ``credentialSubject`` carries ``aiSystemName``, ``frameworks``,
        ``attestations`` and the sections ``posture``, ``redTeam``,
        ``regulatoryClassification``, ``aibom`` (+ ``digest``/``version``),
        ``dataCategories``, ``incidents``, ``models``, ``permissions``,
        ``evidenceRoot`` — each ``{"available": True, "counts": {...}}`` or
        ``{"available": False, "reason": ...}``. Raises ``NotFoundError`` for an
        unknown or soft-deleted AI System.
        """
        return self._http.get(
            f"/trust/passport/ai-systems/{path_segment(ai_system_id, 'ai_system_id')}",
            include_auth=False,
        )

    def fetch_ai_system_verify_bundle(self, ai_system_id: str) -> dict[str, Any]:
        """
        Fetch an AI System's verification bundle: ``{"passport",
        "publicKeyJwk", "verificationHint", "embed": {"badgeUrl", "verifyUrl",
        "html", "markdown"}}`` (be's ``AiSystemTrustPassportVerifyDto``).
        ``GET /trust/passport/ai-systems/{ai_system_id}/verify`` (public — no
        auth). The JWK arrives on the same unauthenticated response as the
        passport, so it is not a trust anchor on its own. Raises
        ``NotFoundError`` (unknown AI System) or a retryable ``ServerError``
        (503 — be could not load the org signing key).
        """
        return self._http.get(
            f"/trust/passport/ai-systems/{path_segment(ai_system_id, 'ai_system_id')}"
            "/verify",
            include_auth=False,
        )

    def fetch_ai_system_badge_svg(self, ai_system_id: str) -> str:
        """
        Fetch an AI System's embeddable SVG trust badge.
        ``GET /trust/passport/ai-systems/{ai_system_id}/badge.svg`` (public —
        no auth). Returns the SVG markup as ``str``. Raises ``NotFoundError``
        for an unknown or soft-deleted AI System.
        """
        r = self._http.stream_get(
            f"/trust/passport/ai-systems/{path_segment(ai_system_id, 'ai_system_id')}"
            "/badge.svg",
            include_auth=False,
        )
        self._http._raise_for_status(r)
        return r.text

    def fetch_ai_system_passport_pdf(self, ai_system_id: str) -> bytes:
        """
        Download the human-readable PDF rendering of an AI System's signed
        trust passport (BE-0541).
        ``GET /trust/passport/ai-systems/{ai_system_id}/passport.pdf`` (public
        — no auth). Returns the raw PDF bytes — write with
        ``open(path, "wb").write(pdf)``. Raises ``NotFoundError`` for an
        unknown or soft-deleted AI System.
        """
        r = self._http.stream_get(
            f"/trust/passport/ai-systems/{path_segment(ai_system_id, 'ai_system_id')}"
            "/passport.pdf",
            include_auth=False,
        )
        self._http._raise_for_status(r)
        return r.content

    def verify_passport(
        self,
        passport: dict[str, Any],
        public_key_jwk: dict[str, Any],
    ) -> dict[str, Any]:
        """Offline-verify a passport (delegates to :func:`verify_passport`)."""
        return verify_passport(passport, public_key_jwk)

    def fetch_and_verify(
        self,
        agent_id: str,
        *,
        trusted_keys: Optional[
            Union[Sequence[dict[str, Any]], Mapping[str, dict[str, Any]]]
        ] = None,
        expected_fingerprint: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Fetch the verification bundle AND verify it offline in one call. Returns
        the verification result with ``passport``, ``publicKeyJwk`` and
        ``didDocumentUrl`` merged in.

        SEC-2026-09-12 MCPSDK-04 — ``GET /trust/passport/{id}/verify`` is a
        PUBLIC, unauthenticated route that returns the passport AND the key that
        "verifies" it. Checking one against the other is self-referential:
        anyone who can answer that request (a TLS-terminating proxy, DNS
        control, a compromised API) can mint a passport plus a matching key. The
        trust anchor therefore has to come from somewhere else:

        :param trusted_keys: public key JWK(s) you resolved out-of-band (DID
            document, vendor onboarding, config) — a sequence, or a mapping
            keyed however you like (e.g. by ``kid``) whose values are the
            anchors. The passport must verify under one of them. Same shape of
            guarantee as :func:`verify_protected_http_result`, whose target key
            is likewise caller-supplied and never taken from the response.
        :param expected_fingerprint: the RFC 7638 SHA-256 JWK thumbprint the
            returned key must match (base64url or hex, optional ``sha256:``
            prefix), for when you can pin the fingerprint but not the key.

        With NO anchor the signature is still checked — ``signatureValid`` stays
        truthful, so a mangled passport is still distinguishable from a
        substituted one — but the result is ``verified: False`` with
        ``reason="unpinned_key"``: an integrity check against an unauthenticated
        key is not an assurance and must not read like one.
        """
        bundle = self.fetch_verify_bundle(agent_id)
        result = _verify_against_anchor(
            bundle["passport"],
            bundle["publicKeyJwk"],
            trusted_keys=trusted_keys,
            expected_fingerprint=expected_fingerprint,
        )
        result["passport"] = bundle["passport"]
        result["publicKeyJwk"] = bundle["publicKeyJwk"]
        result["didDocumentUrl"] = bundle.get("didDocumentUrl")
        return result

    def verify_ai_system_passport(
        self,
        passport: dict[str, Any],
        public_key_jwk: dict[str, Any],
    ) -> dict[str, Any]:
        """Offline-verify an AI System passport (delegates to :func:`verify_ai_system_passport`)."""
        return verify_ai_system_passport(passport, public_key_jwk)

    def fetch_and_verify_ai_system(
        self,
        ai_system_id: str,
        *,
        trusted_keys: Optional[
            Union[Sequence[dict[str, Any]], Mapping[str, dict[str, Any]]]
        ] = None,
        expected_fingerprint: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        :meth:`fetch_and_verify` for an AI System passport:
        ``GET /trust/passport/ai-systems/{ai_system_id}/verify`` (public), then
        :func:`verify_ai_system_passport` under the same trust-anchor rules
        (MCPSDK-04) — no anchor → ``verified: False, reason="unpinned_key"``;
        ``trusted_keys`` without the signing key → ``"untrusted_key"``;
        ``expected_fingerprint`` differing from the served key →
        ``"fingerprint_mismatch"``. Returns the result with ``passport`` and
        ``publicKeyJwk`` merged in.
        """
        bundle = self.fetch_ai_system_verify_bundle(ai_system_id)
        result = _verify_against_anchor(
            bundle["passport"],
            bundle["publicKeyJwk"],
            trusted_keys=trusted_keys,
            expected_fingerprint=expected_fingerprint,
            verify=verify_ai_system_passport,
        )
        result["passport"] = bundle["passport"]
        result["publicKeyJwk"] = bundle["publicKeyJwk"]
        return result


class PraesidiaTrust(TrustResource):
    """
    SDK-0311 — :class:`TrustResource` for third-party verifiers with no Praesidia
    account: no ``api_key``, no ``org_id``, and no ``Authorization`` header on any
    request. Same methods as ``client.trust``; TS parity: ``new PraesidiaTrust()``.

    Args:
        base_url: API base URL; defaults to ``PRAESIDIA_BASE_URL``, then
                  ``https://api.praesidia.ai``.
        timeout:  Per-request timeout in seconds.
        retry:    Retry policy, as for :class:`~praesidia.Praesidia`.

    Example::

        trust = PraesidiaTrust()
        result = trust.fetch_and_verify_ai_system(ai_system_id, trusted_keys=[issuer_jwk])
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        timeout: float = 30.0,
        retry: Union[RetryConfig, bool, None] = None,
    ) -> None:
        if base_url is None:
            base_url = os.environ.get("PRAESIDIA_BASE_URL") or "https://api.praesidia.ai"
        super().__init__(HttpClient.public(base_url, timeout, retry))


# ── Trust anchors for fetch_and_verify (SEC-2026-09-12 MCPSDK-04) ────────────


def jwk_thumbprint(jwk: dict[str, Any]) -> Optional[str]:
    """
    RFC 7638 JWK thumbprint (SHA-256) of an Ed25519 (OKP) or P-256 (EC) public
    key, base64url-encoded without padding. ``None`` for anything else. Use it
    to print the fingerprint of a key you trust and pin it through
    ``fetch_and_verify(..., expected_fingerprint=...)``.
    """
    digest = _thumbprint_digest(jwk)
    return (
        base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        if digest
        else None
    )


def jwk_thumbprint_hex(jwk: dict[str, Any]) -> Optional[str]:
    """Same thumbprint as :func:`jwk_thumbprint`, lowercase hex."""
    digest = _thumbprint_digest(jwk)
    return digest.hex() if digest else None


def _thumbprint_digest(jwk: Any) -> Optional[bytes]:
    if not isinstance(jwk, dict):
        return None
    kty, crv, x = jwk.get("kty"), jwk.get("crv"), jwk.get("x")
    if not isinstance(crv, str) or not isinstance(x, str):
        return None
    # RFC 7638: required members only, lexicographic order, no whitespace.
    if kty == "OKP":
        required = {"crv": crv, "kty": "OKP", "x": x}
    elif kty == "EC" and isinstance(jwk.get("y"), str):
        required = {"crv": crv, "kty": "EC", "x": x, "y": jwk["y"]}
    else:
        return None
    encoded = json.dumps(required, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).digest()


def _jwk_matches_fingerprint(jwk: Any, expected: str) -> bool:
    """
    True when ``expected`` is the RFC 7638 thumbprint of ``jwk``. Accepts
    base64url (padded or not) or hex, with an optional ``sha256:`` prefix, so a
    fingerprint copied from a console, a DID document or the CLI all work.
    """
    digest = _thumbprint_digest(jwk)
    if digest is None:
        return False
    candidate = re.sub(r"^sha-?256:", "", expected.strip(), flags=re.IGNORECASE)
    if not candidate:
        return False
    b64 = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return candidate.rstrip("=") == b64 or candidate.lower() == digest.hex()


def _normalize_trusted_keys(trusted_keys: Any) -> Optional[list[dict[str, Any]]]:
    """Accept both anchor shapes (sequence or mapping) as a flat candidate list."""
    if trusted_keys is None:
        return None
    values = (
        list(trusted_keys.values())
        if isinstance(trusted_keys, Mapping)
        else list(trusted_keys)
    )
    return [key for key in values if isinstance(key, dict)]


def _verify_against_anchor(
    passport: dict[str, Any],
    served_key_jwk: dict[str, Any],
    *,
    trusted_keys: Any = None,
    expected_fingerprint: Optional[str] = None,
    verify: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] = verify_passport,
) -> dict[str, Any]:
    """
    Verify ``passport`` (via ``verify``) against the caller's anchor, falling back to a truthful
    but explicitly unpinned result when no anchor was supplied.

    ``served_key_jwk`` is the key that arrived with the passport; it is only ever
    used to compute an honest ``signatureValid``, or after its fingerprint has
    been pinned by the caller.
    """
    anchors = _normalize_trusted_keys(trusted_keys)
    has_fingerprint = bool(expected_fingerprint)

    if anchors is None and not has_fingerprint:
        # Unpinned: report the real signature/expiry state, deny the assurance.
        # A concrete failure (signature-mismatch, expired, ...) is kept because
        # it is strictly more informative; only an otherwise-"ok" check is
        # downgraded to "unpinned_key".
        served = verify(passport, served_key_jwk)
        served["verified"] = False
        if served["reason"] == "ok":
            served["reason"] = "unpinned_key"
        return served

    if has_fingerprint and not _jwk_matches_fingerprint(
        served_key_jwk, str(expected_fingerprint)
    ):
        served = verify(passport, served_key_jwk)
        served["verified"] = False
        served["reason"] = "fingerprint_mismatch"
        return served

    if anchors is None:
        # Fingerprint-only anchor, and the served key matched it.
        return verify(passport, served_key_jwk)

    # Key anchor: the passport must verify under one of the caller's keys.
    under_trusted_key: Optional[dict[str, Any]] = None
    for anchor in anchors:
        result = verify(passport, anchor)
        if result["verified"]:
            return result
        # Signature is good under a trusted key but something else failed
        # (expired / invalid expiration) — that reason is more useful than
        # "untrusted_key".
        if result["signatureValid"] and under_trusted_key is None:
            under_trusted_key = result
    if under_trusted_key is not None:
        return under_trusted_key
    return _result(False, False, False, "untrusted_key")
