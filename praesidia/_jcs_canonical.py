"""
PA01 D2/D18 — RFC 8785 JSON Canonicalization Scheme, ported from the
reference implementation ``be/src/protected-actions/utils/jcs-canonical.ts``
per ``.claude/tickets/PA01-CONTRACT-action-fixtures.md``. Byte-compared
against the SAME golden fixtures ``be`` publishes
(``tests/fixtures/jcs-golden-fixtures.json``, copied verbatim — see
``tests/test_jcs_canonical.py``).

This is a SEPARATE module from ``_crypto.py``'s ``canonical_json`` — that one
mirrors be-core's FROZEN ``common/security/utils/canonical-json.ts`` (used
for trust-passport signature verification) and deliberately COERCES
non-finite numbers / etc. This module is the NEW, stricter D2 profile used
only for the protected-action request commitment: it RAISES (never silently
coerces) on anything not representable as strict JSON, per corrigendum C2 /
SEC-PA01-03/11. Do not merge the two — that was the exact mistake C2
corrected in ``be``.

PA01 D8/D11 (scope correction, ``.claude/backlog/PA-0013.md``): this SDK
version does not yet use this module to bind a client-side Permit — the
managed MCP path computes the ONE authoritative commitment edge-side (D2).
It is ported now so a TS/Python canonicalization disagreement is caught by a
byte-compare test (this ticket's DoD 4) before EDGE-003 (the
customer-controlled Proof Edge, out of PA01 scope) ever needs an
SDK-computed commitment for real.

Two cross-language hazards this module deliberately gets right (both called
out by the shared golden fixture file):

- **Key sort order.** RFC 8785 mandates UTF-16 CODE-UNIT order. Python's
  default ``sorted()`` compares by Unicode CODE POINT, which diverges from
  that for astral-plane (non-BMP, >= U+10000) keys — the
  ``non-bmp-key-sort-order`` fixture case exists to catch exactly this. This
  module sorts by the ``utf-16-be`` byte encoding of each key (the same fix
  ``_crypto.canonical_json`` already applies for BUGHUNT-SDK-04) to
  reproduce JS ``Array.prototype.sort()`` order exactly, surrogate pairs
  included.
- **Number formatting.** RFC 8785 §3.2.2.3 mandates the exact algorithm
  behind ECMAScript's ``Number::toString`` — which picks a DIFFERENT
  scientific-notation threshold than Python's ``repr``/``json.dumps`` (e.g.
  ``1e-7`` in JS vs Python's ``1e-07``) and formats ``-0`` as ``"0"``. Python
  float ``repr`` already computes the same SHORTEST ROUND-TRIP DIGIT STRING
  as V8 (both use a variant of Grisu/Ryu-family shortest-dtoa), so
  ``_format_number`` reuses ``repr()`` for the digits and reformats them per
  the ECMA-262 Number::toString algorithm's own placement/threshold rules
  rather than trusting Python's own string layout.
"""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from typing import Any


class JcsCanonicalizationError(Exception):
    """Raised when a value cannot be safely RFC-8785-canonicalized."""

    def __init__(self, message: str) -> None:
        super().__init__(f"JCS canonicalization refused: {message}")


#: A value producible by ``json.loads``, and nothing else.
JsonValue = Any


def _has_unpaired_surrogate(s: str) -> bool:
    """
    Detect an unpaired UTF-16 surrogate within a Python ``str``.

    A native Python string stores full code points (no surrogates for
    astral-plane characters), so an "unpaired surrogate" can only arise from
    an explicit lone surrogate code point (U+D800-U+DFFF) present in the
    string — e.g. one produced by a ``\\ud800`` source literal or a
    ``surrogatepass``-decoded byte stream. Mirrors the TS implementation's
    UTF-16-code-unit walk, adapted to Python's code-point strings.
    """
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in s)


def _format_number(v: float) -> str:
    """
    ECMA-262 ``Number::toString`` (RFC 8785 §3.2.2.3), applied to the
    shortest round-trip decimal digit string Python's own float ``repr``
    already computes.
    """
    if v == 0.0:
        # Covers BOTH +0.0 and -0.0 — JS `(-0).toString()` === "0".
        return "0"
    negative = v < 0
    # `repr(abs(v))` is Python's shortest-round-trip decimal text (e.g.
    # '1.0', '1e+21', '1e-07', '0.1'). Decimal(...).as_tuple() decomposes it
    # into (sign, digits, exponent) with NO precision loss and no
    # reformatting surprises, giving exactly `s` (as `digits`) and the power
    # of ten it is scaled by.
    _sign, digits, exponent = Decimal(repr(abs(v))).as_tuple()
    digit_str = "".join(str(d) for d in digits)
    k = len(digit_str)
    # ECMA-262: value == s * 10**(n-k)  <=>  n == k + exponent (since
    # Decimal's `exponent` is defined by value == int(digit_str) * 10**exponent).
    n = k + exponent

    if k <= n <= 21:
        s = digit_str + "0" * (n - k)
    elif 0 < n <= 21:
        s = digit_str[:n] + "." + digit_str[n:]
    elif -6 < n <= 0:
        s = "0." + "0" * (-n) + digit_str
    else:
        exp = n - 1
        mantissa = digit_str[0] + ("." + digit_str[1:] if k > 1 else "")
        s = mantissa + "e" + ("+" if exp >= 0 else "-") + str(abs(exp))
    return ("-" if negative else "") + s


def _canonicalize(v: JsonValue) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        # Must precede the `int` check — `bool` is an `int` subclass.
        return "true" if v else "false"
    if isinstance(v, int):
        return json.dumps(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise JcsCanonicalizationError(
                f"non-finite number is not valid JSON: {v!r}"
            )
        return _format_number(v)
    if isinstance(v, str):
        if _has_unpaired_surrogate(v):
            raise JcsCanonicalizationError(
                "string contains an unpaired UTF-16 surrogate, which cannot "
                "be encoded to well-formed UTF-8 without a lossy substitution"
            )
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(_canonicalize(el) for el in v) + "]"
    if isinstance(v, dict):
        # Python dict keys are ordinary hashable objects — there is no
        # "__proto__ resolves through the prototype chain" trap here (that
        # is JS-specific); a literal "__proto__" string key is always an
        # ordinary own key. Validate BEFORE sorting (the sort key itself
        # calls `.encode()`, which would raise the wrong exception type for
        # a non-string key). Sort by the utf-16-be byte encoding of each key
        # to reproduce JS's UTF-16-code-unit sort order exactly (see module
        # docstring).
        for k in v.keys():
            if not isinstance(k, str):
                raise JcsCanonicalizationError(f"object key {k!r} is not a string")
        keys = sorted(v.keys(), key=lambda k: k.encode("utf-16-be"))
        parts = [
            json.dumps(k, ensure_ascii=False) + ":" + _canonicalize(v[k]) for k in keys
        ]
        return "{" + ",".join(parts) + "}"
    raise JcsCanonicalizationError(
        f"value of type {type(v).__name__} is not a valid JSON value"
    )


def jcs_canonicalize(value: JsonValue) -> bytes:
    """
    Return the RFC 8785 canonical UTF-8 bytes for ``value``. Raises
    :class:`JcsCanonicalizationError` on anything not representable as a
    strict JSON value — see the module docstring for the full, deliberate
    list (non-finite numbers, unpaired surrogates, non-string dict keys, and
    any Python type with no JSON representation).
    """
    return _canonicalize(value).encode("utf-8")


def jcs_commitment(value: JsonValue) -> str:
    """
    ``sha256(JCS(value))``, hex, lowercase — the D2 commitment primitive.
    The ONE shared helper; mirrors ``be``'s single-helper discipline.
    """
    return hashlib.sha256(jcs_canonicalize(value)).hexdigest()
