"""
praesidia — Python management SDK for the Praesidia AI agent platform.

Quick start::

    from praesidia import Praesidia

    client = Praesidia(
        api_key="sk-...",
        org_id="your-org-id",
        base_url="http://localhost:5001",   # omit for hosted API
    )

    # List agents
    agents = client.agents.list()

    # Run an agent task
    task = client.agents.run(
        "00000000-0000-4000-8000-000000000001",
        input={"message": "Hello, agent!"},
    )
    print(task["id"], task["status"])

    # Stream the audit log
    for event in client.audit.stream(from_date="2026-01-01"):
        print(event)
"""

from ._crypto import (
    canonical_json,
    ed25519_public_key_from_jwk,
    ed25519_verify,
    es256_verify,
    p256_public_key_from_jwk,
)
from ._jcs_canonical import (
    JcsCanonicalizationError,
    jcs_canonicalize,
    jcs_commitment,
)
from ._retry import RetryConfig
from .agents import tool_call_headers_from_task
from .client import Praesidia
from .exceptions import (
    AuthError,
    ForbiddenError,
    NotFoundError,
    PraesidiaError,
    ProtectedActionDeniedError,
    RateLimitError,
    ServerError,
    UnsupportedProtectedActionTargetError,
)
from .telemetry import gen_ai_span
from .trust import verify_passport

__all__ = [
    "Praesidia",
    "PraesidiaError",
    "AuthError",
    "ForbiddenError",
    "NotFoundError",
    "RateLimitError",
    "ServerError",
    "tool_call_headers_from_task",
    # H3-02f — standalone offline trust-passport verification (no account needed)
    "verify_passport",
    "ed25519_verify",
    "ed25519_public_key_from_jwk",
    "es256_verify",
    "p256_public_key_from_jwk",
    "canonical_json",
    # H1-02 — OTLP GenAI span builder
    "gen_ai_span",
    # FINDING-4 — bounded retry policy config
    "RetryConfig",
    # PA01 DX-002 — protect_action error taxonomy
    "ProtectedActionDeniedError",
    "UnsupportedProtectedActionTargetError",
    # PA01 D2/D18 — RFC 8785 JCS canonicalization
    "jcs_canonicalize",
    "jcs_commitment",
    "JcsCanonicalizationError",
]

__version__ = "0.3.1"
