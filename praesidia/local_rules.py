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
_ASCII = re.ASCII


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

    for rule in _LOCAL_RULES:
        matched_patterns: list[str] = []
        matched_keywords: list[str] = []

        for pattern in rule.patterns:
            match = pattern.search(content)
            if match:
                matched_patterns.append(match.group(0))

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
