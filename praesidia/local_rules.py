"""
Bundled rule-based guardrail patterns for local / offline mode.

Ported from the TypeScript SDK's `runLocalRules` (`sdk/src/local-rules.ts`) --
kept in lock-step so a given input is classified identically by both SDKs
(see `tests/test_local_rules.py`'s shared parity table). These run
synchronously with zero network calls and cover the most common safety
categories. They are intentionally conservative (low false-positive rate) --
remote guardrails with ML/LLM tiers are available via a connected Praesidia
account.

Returned dicts intentionally use the SAME camelCase field names as the TS
`TriggeredGuardrail` interface and be's `guardrails/validate` response DTO
(`guardrailId`, `guardrailName`, ...), not snake_case -- `Guard.check_input`/
`check_output` must return a structurally identical `CheckResult` shape
whether the check ran locally or against the remote API, so a caller never
has to branch on `local` to know which keys are present.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: JS's `\d` / `\w` / `\b` are ALWAYS ASCII-only in a regex literal, regardless
#: of flags -- there is no way to make them Unicode-aware without an explicit
#: Unicode property escape (`\p{...}`), which none of these patterns use.
#: Python's `re` module defaults the other way: `\d`/`\w`/`\b` are
#: Unicode-aware unless `re.ASCII` is passed. Compiling every pattern with
#: `re.ASCII` is required for behavioural parity -- without it, e.g. Eastern
#: Arabic-Indic digits would trip the SSN/credit-card patterns in Python but
#: never in the TS SDK.
#:
#: REOPENED close-out finding [BLOCKING 1] -- this reasoning does NOT extend
#: to `\s`. Unlike `\d`/`\w`/`\b`, JS's `\s` IS Unicode-aware: it matches NBSP
#: (U+00A0), the Unicode "space separator" category (U+1680, U+2000-U+200A,
#: U+202F, U+205F, U+3000), U+2028/U+2029, and U+FEFF. `re.ASCII` narrows
#: Python's `\s` to strictly the ASCII subset ([ \t\n\r\f\v]) -- narrower than
#: JS's, in the dangerous direction: a single non-breaking space between
#: "ignore" and "previous" defeats every `\s`-based prompt-injection pattern
#: below in Python while the TS guard still blocks it. Dropping `re.ASCII`
#: entirely would fix `\s` but reopen the `\d`/`\w`/`\b` divergence. Instead,
#: translate JS's Unicode-only whitespace code points to a plain ASCII space
#: (`_normalize_whitespace_for_matching`) BEFORE running the (still
#: `re.ASCII`-compiled) patterns against the content -- a length-preserving,
#: one-to-one substitution, so match spans still index correctly back into
#: the original string for the reported evidence text.
_ASCII = re.ASCII

#: The members of JS's regex `\s` class that are NOT in Python's `re.ASCII`
#: `\s` class (see MDN's `\s` reference for the canonical code-point list).
_JS_UNICODE_ONLY_WHITESPACE = (
    "\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009"
    "\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
)
_WHITESPACE_TRANSLATION = str.maketrans({ch: " " for ch in _JS_UNICODE_ONLY_WHITESPACE})


def _normalize_whitespace_for_matching(content: str) -> str:
    """
    Translate the Unicode-only members of JS's `\\s` class to a plain ASCII
    space, so an `re.ASCII`-compiled `\\s` pattern matches them too. This is
    a one-to-one code-point substitution (never changes string length), so a
    match's `.start()`/`.end()` on the normalized string are still valid
    offsets into the original `content`.
    """
    return content.translate(_WHITESPACE_TRANSLATION)


def _pattern(source: str, *, ignore_case: bool = False) -> re.Pattern[str]:
    flags = _ASCII | (re.IGNORECASE if ignore_case else 0)
    return re.compile(source, flags)


@dataclass(frozen=True)
class _LocalRule:
    id: str
    name: str
    category: str
    severity: str  # "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
    patterns: tuple[re.Pattern[str], ...] = field(default_factory=tuple)
    keywords: tuple[str, ...] = field(default_factory=tuple)


_LOCAL_RULES: tuple[_LocalRule, ...] = (
    _LocalRule(
        id="local-prompt-injection",
        name="Prompt Injection (local)",
        category="prompt_injection",
        severity="HIGH",
        patterns=(
            _pattern(
                r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)",
                ignore_case=True,
            ),
            _pattern(
                r"disregard\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)",
                ignore_case=True,
            ),
            _pattern(
                r"forget\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)",
                ignore_case=True,
            ),
            _pattern(
                r"you\s+are\s+now\s+(a\s+)?(different|new|evil|unfiltered|uncensored)",
                ignore_case=True,
            ),
            _pattern(
                r"act\s+as\s+(if\s+you\s+(are|were)\s+)?(dan|do anything now|jailbreak)",
                ignore_case=True,
            ),
            _pattern(r"\bDAN\b.*no\s+restrictions", ignore_case=True),
            _pattern(r"system\s*:\s*you\s+are", ignore_case=True),
        ),
    ),
    _LocalRule(
        id="local-pii-ssn",
        name="PII — Social Security Number (local)",
        category="pii",
        severity="HIGH",
        patterns=(
            _pattern(r"\b\d{3}-\d{2}-\d{4}\b"),
            _pattern(r"\b\d{9}\b(?=\s*\b(ssn|social)\b)", ignore_case=True),
        ),
    ),
    _LocalRule(
        id="local-pii-credit-card",
        name="PII — Credit Card Number (local)",
        category="pii",
        severity="HIGH",
        # Luhn-adjacent: 13-19 digit sequences with optional spaces/dashes
        patterns=(_pattern(r"\b(?:\d[ -]?){13,19}\b"),),
    ),
    _LocalRule(
        id="local-hate-speech",
        name="Hate Speech (local)",
        category="hate_speech",
        severity="HIGH",
        # Intentionally minimal list -- full ML-based detection in connected mode
        keywords=("kill all", "exterminate", "genocide"),
    ),
    _LocalRule(
        id="local-violence",
        name="Violence / Threats (local)",
        category="violence",
        severity="MEDIUM",
        patterns=(
            _pattern(
                r"i\s+(will|am going to|gonna)\s+(kill|murder|harm|attack|shoot)\s+(you|him|her|them)",
                ignore_case=True,
            ),
        ),
    ),
)


def run_local_rules(content: str) -> dict[str, Any]:
    """
    Run the bundled local rules against ``content``.

    Zero network calls -- pure regex/keyword evaluation. Returns a
    ``CheckResult``-shaped dict (``passed``, ``triggered``, ``local: True``).
    """
    triggered: list[dict[str, Any]] = []
    # REOPENED close-out finding [BLOCKING 1] -- match against a whitespace-
    # normalized copy so the re.ASCII-compiled `\s` patterns see JS's wider
    # whitespace class too, but slice the ORIGINAL `content` for the
    # reported evidence text (the substitution is one-to-one, so offsets
    # from `normalized` are still valid into `content`).
    normalized = _normalize_whitespace_for_matching(content)

    for rule in _LOCAL_RULES:
        matched_patterns: list[str] = []
        matched_keywords: list[str] = []

        for pattern in rule.patterns:
            match = pattern.search(normalized)
            if match:
                matched_patterns.append(content[match.start() : match.end()])

        lower = content.lower()
        for keyword in rule.keywords:
            if keyword.lower() in lower:
                matched_keywords.append(keyword)

        if matched_patterns or matched_keywords:
            triggered.append(
                {
                    "guardrailId": rule.id,
                    "guardrailName": rule.name,
                    "category": rule.category,
                    "severity": rule.severity,
                    "action": "BLOCK",
                    "reason": "Matched local rule-based pattern",
                    "matchedPatterns": matched_patterns,
                    "matchedKeywords": matched_keywords,
                }
            )

    return {"passed": len(triggered) == 0, "triggered": triggered, "local": True}
