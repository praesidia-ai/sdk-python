"""SDK-2801 — small-order / non-canonical Ed25519 guard (mirrors be BE-2858).

The guard runs on the public key AND on R before the curve maths, so its
verdict must not depend on the underlying verifier: (a) mocks that verifier to
always succeed; (b) derives the 8-torsion from scratch and checks it against
the constant table.
"""

from __future__ import annotations

import hashlib

import pytest

from praesidia import _crypto
from praesidia._crypto import (
    _ED25519_SMALL_ORDER_Y,
    _is_rejected_ed25519_point,
    ed25519_verify,
)

P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
D = (-121665 * pow(121666, P - 2, P)) % P
SQRT_M1 = pow(2, (P - 1) // 4, P)
IDENTITY = (0, 1)


def _add(a: tuple[int, int], b: tuple[int, int]) -> tuple[int, int]:
    (x1, y1), (x2, y2) = a, b
    t = D * x1 * x2 * y1 * y2 % P
    return (
        (x1 * y2 + x2 * y1) * pow(1 + t, P - 2, P) % P,
        (y1 * y2 + x1 * x2) * pow(1 - t, P - 2, P) % P,
    )


def _mul(p: tuple[int, int], k: int) -> tuple[int, int]:
    q = IDENTITY
    while k:
        if k & 1:
            q = _add(q, p)
        p = _add(p, p)
        k >>= 1
    return q


def _point_with_y(y: int) -> tuple[int, int] | None:
    xx = (y * y - 1) * pow(D * y * y + 1, P - 2, P) % P
    x = pow(xx, (P + 3) // 8, P)
    if (x * x - xx) % P:
        x = x * SQRT_M1 % P
    return (x, y) if (x * x - xx) % P == 0 else None


def _derive_torsion() -> list[tuple[int, int]]:
    """The full 8-torsion subgroup: the orbit of [L]·P for a curve point P."""
    for y in range(2, 100):
        p = _point_with_y(y)
        if p is None:
            continue
        t = _mul(p, L)
        orbit, q = [], IDENTITY
        for _ in range(8):
            if q not in orbit:
                orbit.append(q)
            q = _add(q, t)
        if len(orbit) == 8:
            return orbit
    raise AssertionError("no order-8 generator found")


def _encode(y: int, sign: int) -> bytes:
    return (y | (sign << 255)).to_bytes(32, "little")


TORSION = _derive_torsion()
SMALL_ORDER = [_encode(y, x & 1) for x, y in TORSION]
SMALL_ORDER_BOTH_SIGNS = [_encode(y, s) for _x, y in TORSION for s in (0, 1)]
NON_CANONICAL = [_encode(y, s) for y in (P, P + 1, 2**255 - 1) for s in (0, 1)]


def _real_key_and_sig(message: bytes) -> tuple[bytes, bytes]:
    """RFC 8032 §5.1.6 signing with the SDK's own curve helpers (no new dep)."""
    h = hashlib.sha512(b"\x07" * 32).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    pub = _crypto._encode_point(_crypto._scalarmult(_crypto._B, a))
    r = _crypto._sha512(h[32:] + message) % L
    r_enc = _crypto._encode_point(_crypto._scalarmult(_crypto._B, r))
    k = _crypto._sha512(r_enc + pub + message) % L
    s = (r + k * a) % L
    return pub, r_enc + s.to_bytes(32, "little")


def test_constant_table_equals_derived_torsion_y_set():
    assert len(TORSION) == 8
    assert all(_mul(p, 8) == IDENTITY for p in TORSION)
    derived = {y for _x, y in TORSION}
    assert len(derived) == 5
    assert set(_ED25519_SMALL_ORDER_Y) == derived


@pytest.mark.parametrize("enc", SMALL_ORDER_BOTH_SIGNS + NON_CANONICAL)
def test_guard_flags_small_order_and_non_canonical(enc: bytes):
    assert _is_rejected_ed25519_point(enc) is True


def test_guard_passes_ordinary_points():
    pub, sig = _real_key_and_sig(b"x")
    assert _is_rejected_ed25519_point(pub) is False
    assert _is_rejected_ed25519_point(sig[:32]) is False
    assert _is_rejected_ed25519_point(_encode(P - 2, 0)) is False


def test_rejects_bad_key_and_r_even_if_underlying_verify_accepts(monkeypatch):
    monkeypatch.setattr(_crypto, "_ed25519_verify_unguarded", lambda *_: True)
    msg = b"msg"
    pub, sig = _real_key_and_sig(msg)
    assert len(SMALL_ORDER) == 8
    for enc in SMALL_ORDER + NON_CANONICAL:
        assert ed25519_verify(msg, sig, enc) is False  # as public key
        assert ed25519_verify(msg, enc + sig[32:], pub) is False  # as R
    # the guard does not swallow honest inputs: the mocked verifier is reached
    assert ed25519_verify(msg, sig, pub) is True


def test_real_key_still_verifies_with_real_maths():
    pub, sig = _real_key_and_sig(b"praesidia")
    assert ed25519_verify(b"praesidia", sig, pub) is True
    assert ed25519_verify(b"praesidiA", sig, pub) is False


def test_all_zero_key_and_signature_never_verify():
    assert not any(
        ed25519_verify(f"m{i}".encode(), bytes(64), bytes(32)) for i in range(50)
    )
