"""
H3-02f — hand-written offline verification primitives for the trust-passport
verify client. NOT part of the typed API surface.

Pure-Python, ZERO dependencies (stdlib ``hashlib`` only). Python 3.9's stdlib
has neither Ed25519 nor ECDSA verification primitives, and the SDK's only
runtime dependency is ``httpx`` — so rather than pull in ``cryptography`` we
vendor compact verification-only implementations for the two algorithms
be-core emits. This keeps ``pip install praesidia`` dependency-light while a
third party verifies a passport against a caller-trusted public key.

Byte-for-byte compatible with be-core's signing path:
- :func:`canonical_json`  mirrors be-core ``canonicalJson`` (AGV-030 / JCS-style)
  — object keys sorted lexicographically; the exact bytes the passport ``proof``
  is signed over.
- :func:`ed25519_verify`  RFC 8032 Ed25519 (PureEdDSA over edwards25519, SHA-512).
- :func:`ed25519_public_key_from_jwk`  decodes an OKP/Ed25519 JWK's base64url
  ``x`` coordinate into the raw 32-byte public key.
- :func:`es256_verify`  verifies the strict low-s DER ECDSA-P256 signatures
  returned by AWS KMS (``ECDSA_SHA_256``).
- :func:`p256_public_key_from_jwk`  validates an EC/P-256 JWK and returns the
  affine public point.
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

# ── NIST P-256 / ES256 constants (FIPS 186-4) ──────────────────────────────
_P256_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
_P256_A = _P256_P - 3
_P256_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
_P256_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_P256_HALF_N = _P256_N >> 1
_P256_G = (
    0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
    0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5,
)


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
    if not _is_verification_jwk(jwk, "EdDSA"):
        return None
    x = jwk.get("x")
    if not isinstance(x, str) or len(x) != 43:
        return None
    try:
        raw = _b64url_decode(x)
    except Exception:
        return None
    if len(raw) != 32:
        return None
    return raw


def p256_public_key_from_jwk(jwk: Any) -> Optional[tuple[int, int]]:
    """
    Validate an EC/P-256 public JWK and return its affine ``(x, y)`` point.

    Optional ``alg``/``use``/``key_ops`` metadata must describe an ES256
    verification key. Private-key material and off-curve points are rejected.
    Returns ``None`` on every malformed input.
    """
    if not isinstance(jwk, dict):
        return None
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        return None
    if not _is_verification_jwk(jwk, "ES256"):
        return None
    x_text = jwk.get("x")
    y_text = jwk.get("y")
    if (
        not isinstance(x_text, str)
        or len(x_text) != 43
        or not isinstance(y_text, str)
        or len(y_text) != 43
    ):
        return None
    try:
        x_raw = _b64url_decode(x_text)
        y_raw = _b64url_decode(y_text)
    except (ValueError, TypeError):
        return None
    if len(x_raw) != 32 or len(y_raw) != 32:
        return None
    x = int.from_bytes(x_raw, "big")
    y = int.from_bytes(y_raw, "big")
    if x >= _P256_P or y >= _P256_P:
        return None
    if (y * y - (pow(x, 3, _P256_P) + _P256_A * x + _P256_B)) % _P256_P:
        return None
    return (x, y)


def es256_verify(
    message: bytes,
    signature: bytes,
    public_key: tuple[int, int],
) -> bool:
    """
    Verify a KMS-style ES256 signature over ``message``.

    ``signature`` must be canonical ASN.1 DER ``(r, s)`` with low-s enforced,
    matching be-core's malleability gate. Returns ``False`` (never raises) for
    malformed signatures, invalid/off-curve public points, or a mismatch.
    """
    try:
        decoded = _decode_p256_der_signature(signature)
        if decoded is None:
            return False
        r, s = decoded
        if not 1 <= s <= _P256_HALF_N:
            return False
        if not _p256_is_on_curve(public_key):
            return False
        digest = int.from_bytes(hashlib.sha256(message).digest(), "big")
        w = pow(s, -1, _P256_N)
        point = _p256_add(
            _p256_scalarmult(_P256_G, (digest * w) % _P256_N),
            _p256_scalarmult(public_key, (r * w) % _P256_N),
        )
        return point is not None and point[0] % _P256_N == r
    except Exception:
        return False


def _is_verification_jwk(jwk: dict[str, Any], algorithm: str) -> bool:
    if jwk.get("alg", algorithm) != algorithm:
        return False
    if jwk.get("use", "sig") != "sig" or "d" in jwk:
        return False
    key_ops = jwk.get("key_ops")
    return key_ops is None or key_ops == ["verify"]


def _decode_p256_der_signature(signature: bytes) -> Optional[tuple[int, int]]:
    if (
        not isinstance(signature, bytes)
        or not 8 <= len(signature) <= 72
        or signature[0] != 0x30
        or signature[1] != len(signature) - 2
    ):
        return None
    r_read = _read_der_integer(signature, 2)
    if r_read is None:
        return None
    r, offset = r_read
    s_read = _read_der_integer(signature, offset)
    if s_read is None:
        return None
    s, offset = s_read
    if offset != len(signature):
        return None
    if not 1 <= r < _P256_N or not 1 <= s < _P256_N:
        return None
    return (r, s)


def _read_der_integer(data: bytes, offset: int) -> Optional[tuple[int, int]]:
    if offset + 2 > len(data) or data[offset] != 0x02:
        return None
    length = data[offset + 1]
    if not 1 <= length <= 33:
        return None
    start = offset + 2
    end = start + length
    if end > len(data):
        return None
    magnitude = data[start:end]
    if magnitude[0] & 0x80:
        return None
    if len(magnitude) > 1 and magnitude[0] == 0:
        if not magnitude[1] & 0x80:
            return None
        magnitude = magnitude[1:]
    if len(magnitude) > 32:
        return None
    return (int.from_bytes(magnitude, "big"), end)


def _p256_is_on_curve(point: tuple[int, int]) -> bool:
    if not isinstance(point, tuple) or len(point) != 2:
        return False
    x, y = point
    if not isinstance(x, int) or not isinstance(y, int):
        return False
    if not 0 <= x < _P256_P or not 0 <= y < _P256_P:
        return False
    return (y * y - (pow(x, 3, _P256_P) + _P256_A * x + _P256_B)) % _P256_P == 0


def _p256_add(
    left: Optional[tuple[int, int]],
    right: Optional[tuple[int, int]],
) -> Optional[tuple[int, int]]:
    if left is None:
        return right
    if right is None:
        return left
    x1, y1 = left
    x2, y2 = right
    if x1 == x2:
        if (y1 + y2) % _P256_P == 0:
            return None
        slope = ((3 * x1 * x1 + _P256_A) * pow(2 * y1, -1, _P256_P)) % _P256_P
    else:
        slope = ((y2 - y1) * pow((x2 - x1) % _P256_P, -1, _P256_P)) % _P256_P
    x3 = (slope * slope - x1 - x2) % _P256_P
    y3 = (slope * (x1 - x3) - y1) % _P256_P
    return (x3, y3)


def _p256_scalarmult(
    point: tuple[int, int], scalar: int
) -> Optional[tuple[int, int]]:
    result: Optional[tuple[int, int]] = None
    addend: Optional[tuple[int, int]] = point
    while scalar > 0:
        if scalar & 1:
            result = _p256_add(result, addend)
        addend = _p256_add(addend, addend)
        scalar >>= 1
    return result


def _b64url_decode(value: str) -> bytes:
    if not _BASE64URL_RE.fullmatch(value):
        raise ValueError("non-canonical base64url")
    padding = "=" * (-len(value) % 4)
    decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii")
    if canonical != value:
        raise ValueError("non-canonical base64url")
    return decoded


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
