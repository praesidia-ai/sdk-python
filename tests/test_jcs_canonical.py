"""
PA01 DoD 4 — byte-compare every non-throw golden fixture case, confirm every
``expectThrow`` case raises. This is the ONE test that would catch a
TS/Python canonicalization disagreement before it silently breaks a Permit
binding (see the fixture file's own ``notes`` field and
``.claude/tickets/PA01-CONTRACT-action-fixtures.md``).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from praesidia._jcs_canonical import (
    JcsCanonicalizationError,
    jcs_canonicalize,
    jcs_commitment,
)

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "jcs-golden-fixtures.json"
FIXTURES = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
CASES = FIXTURES["cases"]

NON_THROW_CASES = [c for c in CASES if not c.get("expectThrow")]
THROW_CASES = [c for c in CASES if c.get("expectThrow")]


@pytest.mark.parametrize("case", NON_THROW_CASES, ids=lambda c: c["name"])
def test_golden_fixture_byte_compare(case: dict) -> None:
    value = case["input"]
    canonical_bytes = jcs_canonicalize(value)
    assert canonical_bytes.decode("utf-8") == case["canonicalUtf8"]
    assert jcs_commitment(value) == case["sha256Hex"]


@pytest.mark.parametrize("case", THROW_CASES, ids=lambda c: c["name"])
def test_golden_fixture_refusals(case: dict) -> None:
    # "non-finite-number-nan" is represented as a sentinel string in the
    # on-disk JSON (NaN is not valid JSON) — substitute the real NaN before
    # calling the implementation, per the fixture case's own `note`.
    if case["name"] == "non-finite-number-nan":
        value = {"n": float("nan")}
    else:
        value = case["input"]
    with pytest.raises(JcsCanonicalizationError) as excinfo:
        jcs_canonicalize(value)
    reason = case.get("throwReasonContains")
    if reason:
        assert reason in str(excinfo.value)


def test_negative_zero_in_memory() -> None:
    """
    The fixture file's own note: its on-disk `-0` text cannot be trusted to
    survive a generic JSON-loading pipeline with the sign bit intact
    (Python's own `json.loads('-0')` yields a plain `int` 0 with no sign
    concept at all), so this module additionally unit-tests in-memory `-0.0`
    handling directly.
    """
    assert jcs_canonicalize({"n": -0.0}).decode("utf-8") == '{"n":0}'


def test_positive_zero_float_also_formats_as_bare_zero() -> None:
    assert jcs_canonicalize({"n": 0.0}).decode("utf-8") == '{"n":0}'


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.0, "1"),
        (-1.0, "-1"),
        (123.0, "123"),
        (1.25, "1.25"),
        (100.01, "100.01"),
        (1.0000000000000002, "1.0000000000000002"),
        (1e-7, "1e-7"),
        (1e-6, "0.000001"),
        (1e20, "100000000000000000000"),
        (1e21, "1e+21"),
    ],
)
def test_float_matches_ecmascript_number_encoding(value: float, expected: str) -> None:
    assert jcs_canonicalize({"n": value}) == ('{"n":' + expected + "}").encode()


@pytest.mark.parametrize(
    "key", [pytest.param(chr(0xD800), id="high"), pytest.param(chr(0xDFFF), id="low")]
)
def test_unpaired_surrogate_dict_key_is_refused_with_canonicalization_error(
    key: str,
) -> None:
    with pytest.raises(JcsCanonicalizationError):
        jcs_canonicalize({key: "value"})


def test_top_level_none_is_null_not_a_refusal() -> None:
    # None/null IS a valid JSON value — only Python's absence of a key (no
    # analogue to JS `undefined`) is out of scope here (Python has no
    # `undefined`, so there is no equivalent refusal case to port).
    assert jcs_canonicalize(None) == b"null"


def test_infinity_is_refused() -> None:
    with pytest.raises(JcsCanonicalizationError):
        jcs_canonicalize({"n": math.inf})


def test_negative_infinity_is_refused() -> None:
    with pytest.raises(JcsCanonicalizationError):
        jcs_canonicalize({"n": -math.inf})


def test_non_string_dict_key_is_refused() -> None:
    with pytest.raises(JcsCanonicalizationError):
        jcs_canonicalize({1: "x"})  # type: ignore[dict-item]


def test_unsupported_type_is_refused() -> None:
    with pytest.raises(JcsCanonicalizationError):
        jcs_canonicalize(object())
