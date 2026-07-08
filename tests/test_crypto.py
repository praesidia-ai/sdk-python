"""Tests for praesidia._crypto.canonical_json key ordering (BUGHUNT-SDK-04).

The canonicalizer is the signing-preimage reconstructor for
trust.verify_passport; its key sort MUST match be-core / the TS clients
(``Object.keys().sort()`` — UTF-16 code-unit order) byte-for-byte or a
genuinely valid passport with dynamic object keys is falsely rejected.
"""

from __future__ import annotations

from praesidia._crypto import canonical_json


def test_astral_key_sorts_by_utf16_code_unit_like_js():
    # Keys: U+FFFF (￿) and U+1F600 (😀). Under UTF-16 code-unit order (V8 /
    # Array.prototype.sort, which be-core uses) the astral key's lead
    # surrogate 0xD83D sorts BELOW 0xFFFF, so 😀 comes FIRST. Python's old
    # code-point sort put ￿ first (65535 < 128512) — the divergence fixed.
    out = canonical_json({"￿": 1, "\U0001F600": 2})
    assert out == b'{"\xf0\x9f\x98\x80":2,"\xef\xbf\xbf":1}'


def test_bmp_keys_unaffected():
    # Pure-ASCII / '@' keys (the entire current passport schema) sort
    # identically under both orderings — proves the fix is non-regressing.
    out = canonical_json({"b": 1, "a": 2, "@context": 3})
    assert out == b'{"@context":3,"a":2,"b":1}'


def test_astral_and_bmp_keys_interleave_like_js():
    # Mix: '\U0001F600' (astral, lead unit 0xD83D), '' (BMP 0xE000),
    # '￿' (BMP 0xFFFF). JS UTF-16 order: 0xD83D < 0xE000 < 0xFFFF.
    out = canonical_json({"￿": 1, "": 2, "\U0001F600": 3})
    assert out == '{"\U0001F600":3,"":2,"￿":1}'.encode("utf-8")


def test_nested_dynamic_keys_are_sorted_at_every_level():
    # A caller-influenced metadata map keyed by capability names mixing
    # astral + BMP keys must canonicalize deterministically at depth.
    doc = {"credentialSubject": {"￿": 1, "\U0001F600": 2, "z": 3}}
    out = canonical_json(doc)
    # 'z' (0x7A) < astral (0xD83D) < ￿ (0xFFFF).
    assert out == (
        '{"credentialSubject":{"z":3,"\U0001F600":2,"￿":1}}'.encode("utf-8")
    )
