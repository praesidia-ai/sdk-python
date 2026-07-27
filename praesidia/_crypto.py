"""
H3-02f — hand-written offline verification primitives for the trust-passport
verify client. NOT part of the typed API surface.

Pure-Python, ZERO dependencies (stdlib ``hashlib`` only). Python 3.9's stdlib
has no Ed25519 primitive, and the SDK's only runtime dependency is ``httpx`` —
so rather than pull in ``cryptography`` we vendor a compact RFC 8032 Ed25519
*verify* here (the same "zero non-built-in dependencies" ethos as the
``@praesidia/audit-verifier`` package). This keeps ``pip install praesidia``
dependency-light while still letting a third party verify an agent's reputation
OFFLINE, without trusting Praesidia.

Byte-for-byte compatible with be-core's signing path:
- :func:`canonical_json`  mirrors be-core ``canonicalJson`` (AGV-030 / JCS-style)
  — object keys sorted lexicographically; the exact bytes the passport ``proof``
  is signed over.
- :func:`ed25519_verify`  RFC 8032 Ed25519 (PureEdDSA over edwards25519, SHA-512).
- :func:`ed25519_public_key_from_jwk`  decodes an OKP/Ed25519 JWK's base64url
  ``x`` coordinate into the raw 32-byte public key.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
from typing import Any, Optional

# ── edwards25519 curve constants (RFC 8032 §5.1) ───────────────────────────
_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_I = pow(2, (_P - 1) // 4, _P)  # sqrt(-1)


def _sha512(data: bytes) -> int:
    return int.from_bytes(hashlib.sha512(data).digest(), "little")


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


# Base point B (RFC 8032 §5.1).
def _x_recover(y: int) -> int:
    xx = (y * y - 1) * _inv(_D * y * y + 1)
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = (x * _I) % _P
    if x % 2 != 0:
        x = _P - x
    return x


_BY = (4 * _inv(5)) % _P
_BX = _x_recover(_BY)
_B = (_BX % _P, _BY % _P, 1, (_BX * _BY) % _P)  # extended coords (X, Y, Z, T)
_IDENTITY_ENCODING = bytes([1]) + bytes(31)
_BASE64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _edwards_add(p: tuple, q: tuple) -> tuple:
    x1, y1, z1, t1 = p
    x2, y2, z2, t2 = q
    a = ((y1 - x1) * (y2 - x2)) % _P
    b = ((y1 + x1) * (y2 + x2)) % _P
    c = (t1 * 2 * _D * t2) % _P
    dd = (z1 * 2 * z2) % _P
    e = b - a
    f = dd - c
    g = dd + c
    h = b + a
    x3 = (e * f) % _P
    y3 = (g * h) % _P
    t3 = (e * h) % _P
    z3 = (f * g) % _P
    return (x3, y3, z3, t3)


def _scalarmult(p: tuple, e: int) -> tuple:
    q = (0, 1, 1, 0)  # neutral element
    while e > 0:
        if e & 1:
            q = _edwards_add(q, p)
        p = _edwards_add(p, p)
        e >>= 1
    return q


def _encode_point(p: tuple) -> bytes:
    x, y, z, _t = p
    zi = _inv(z)
    x = (x * zi) % _P
    y = (y * zi) % _P
    val = y | ((x & 1) << 255)
    return val.to_bytes(32, "little")


def _decode_point(s: bytes) -> Optional[tuple]:
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little") & ((1 << 255) - 1)
    if y >= _P:
        return None
    x = _x_recover(y)
    if (int.from_bytes(s, "little") >> 255) & 1 != (x & 1):
        x = _P - x
    p = (x % _P, y % _P, 1, (x * y) % _P)
    # Verify the point is on the curve: -x^2 + y^2 = 1 + d x^2 y^2.
    if (-x * x + y * y - 1 - _D * x * x * y * y) % _P != 0:
        return None
    # Strict verification requires prime-order subgroup points. Otherwise the
    # identity key accepts the trivial R=identity, S=0 signature for any input.
    if _encode_point(p) == _IDENTITY_ENCODING:
        return None
    if _encode_point(_scalarmult(p, _L)) != _IDENTITY_ENCODING:
        return None
    return p


def ed25519_verify(message: bytes, signature: bytes, public_key: bytes) -> bool:
    """
    Verify an RFC 8032 Ed25519 signature. Returns ``False`` (never raises) on any
    malformed input or mismatched signature.

    Args:
        message:    The exact bytes that were signed.
        signature:  The 64-byte Ed25519 signature (raw, not base64).
        public_key: The raw 32-byte Ed25519 public key.
    """
    try:
        if len(signature) != 64 or len(public_key) != 32:
            return False
        a = _decode_point(public_key)
        if a is None:
            return False
        r_bytes = signature[:32]
        r = _decode_point(r_bytes)
        if r is None:
            return False
        s = int.from_bytes(signature[32:], "little")
        if s >= _L:
            return False
        h = _sha512(r_bytes + public_key + message) % _L
        # Check [s]B == R + [h]A
        left = _scalarmult(_B, s)
        right = _edwards_add(r, _scalarmult(a, h))
        return _encode_point(left) == _encode_point(right)
    except Exception:
        return False


def ed25519_public_key_from_jwk(jwk: Any) -> Optional[bytes]:
    """
    Decode an OKP/Ed25519 JWK into the raw 32-byte public key.

    be-core emits ``{"kty": "OKP", "crv": "Ed25519", "use": "sig", "x": <b64url>}``.
    Returns ``None`` for any JWK that is not a well-formed Ed25519 public key so
    verification fails closed.
    """
    if not isinstance(jwk, dict):
        return None
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        return None
    x = jwk.get("x")
    if not isinstance(x, str) or not x:
        return None
    try:
        raw = _b64url_decode(x)
    except Exception:
        return None
    if len(raw) != 32:
        return None
    return raw


def _b64url_decode(value: str) -> bytes:
    if not _BASE64URL_RE.fullmatch(value):
        raise ValueError("non-canonical base64url")
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def canonical_json(value: Any) -> bytes:
    """
    Deterministic JSON byte encoding — byte-for-byte identical to be-core's
    ``canonicalJson`` (AGV-030). Object keys are sorted by their UTF-16
    code-unit sequence (exactly matching V8 / ``Array.prototype.sort``, which
    be-core and the TS clients use), ``None`` collapses to ``null``, and
    separators carry no whitespace.

    BUGHUNT-SDK-04 — the key sort is by UTF-16 code UNIT, not Unicode code
    POINT. The two agree only within the BMP (≤ U+FFFF): an astral-plane key
    (≥ U+10000) is a surrogate pair whose lead unit (0xD800–0xDBFF) sorts
    BELOW U+E000–U+FFFF under UTF-16, but ABOVE them under code-point order.
    Sorting by the ``utf-16-be`` byte sequence reproduces JS ordering for
    surrogate pairs too, so the Python verifier reconstructs the SAME signing
    preimage be-core signed even for caller-influenced (dynamic) object keys.

    This reproduces the exact bytes the trust-passport ``proof`` was signed over
    (the passport document with its ``proof`` member removed).
    """
    return _canonicalize(value).encode("utf-8")


def _canonicalize(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            raise ValueError("canonical_json: non-finite number")
        # json.dumps matches JSON.stringify for the integer / finite-float cases
        # that appear in a passport (trust score is an int).
        return json.dumps(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(_canonicalize(x) for x in v) + "]"
    if isinstance(v, dict):
        # BUGHUNT-SDK-04 — sort by the UTF-16-BE byte sequence to reproduce
        # JS ``Object.keys().sort()`` (UTF-16 code-unit order) EXACTLY,
        # surrogate pairs included. Python's default ``sorted()`` compares by
        # Unicode code point, which diverges from V8 for astral (non-BMP)
        # keys — a divergent key order yields different canonical bytes and a
        # spurious signature mismatch on an otherwise-valid passport.
        keys = sorted(v.keys(), key=lambda k: k.encode("utf-16-be"))
        parts = [
            json.dumps(k, ensure_ascii=False) + ":" + _canonicalize(v[k])
            for k in keys
        ]
        return "{" + ",".join(parts) + "}"
    return "null"
