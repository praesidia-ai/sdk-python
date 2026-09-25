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

__version__ = "0.4.1"

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
# SDK-0002 — AI System / asset / relationship graph parity with be's
# AISYS-0002 and the TS SDK's ai_systems resource (SDK-0001).
from .ai_systems import AiSystemsResource
from .client import Praesidia
# SDK-0313 — tag gateway calls with an MCP server id (GW-0776).
from .gateway import MCP_SERVER_ID_HEADER, gateway_headers
from .guard import Guard, TaskHandle
from .identity import IdentityClient, IdentityError
from .local_rules import run_local_rules
# SDK-0301 — advisory in-runtime interaction hooks (parity with the TS SDK's SDK-0300).
from .interaction_hooks import (
    DEFAULT_FAIL_MODES,
    INTERACTION_OUTCOME_STATUSES,
    INTERACTION_TYPES,
    INTERACTION_VERDICTS,
    AsyncPraesidiaInteractionHooks,
    InteractionDecision,
    InteractionHookResult,
    InteractionOutcomeReceipt,
    PraesidiaInteractionHooks,
)
from .exceptions import (
    AuthError,
    ForbiddenError,
    GuardrailBlockedError,
    InteractionDecisionUnavailableError,
    InteractionDeniedError,
    InvalidMcpServerIdError,
    NotFoundError,
    PraesidiaConfigError,
    PraesidiaError,
    ProtectedActionDeniedError,
    RateLimitError,
    ResponseTooLargeError,
    ServerError,
    UnsupportedProtectedActionTargetError,
)
from .telemetry import gen_ai_span
from .trust import (
    PraesidiaTrust,
    jwk_thumbprint,
    jwk_thumbprint_hex,
    verify_ai_system_passport,
    verify_passport,
)

__all__ = [
    "IdentityClient",
    "IdentityError",
    "Praesidia",
    "AiSystemsResource",
    "PraesidiaError",
    "AuthError",
    "ForbiddenError",
    "NotFoundError",
    "RateLimitError",
    "ResponseTooLargeError",
    "ServerError",
    "tool_call_headers_from_task",
    # SDK-0313 — gateway MCP server id header
    "gateway_headers",
    "MCP_SERVER_ID_HEADER",
    "InvalidMcpServerIdError",
    # TOP-0008 -- Guard convenience wrapper + offline local-rules guardrail fallback
    "Guard",
    "TaskHandle",
    "GuardrailBlockedError",
    "PraesidiaConfigError",
    "run_local_rules",
    # H3-02f — standalone offline trust-passport verification (no account needed)
    "verify_passport",
    # SDK-0311 — trust-passport fetches with no API key / org (third-party verifiers)
    "PraesidiaTrust",
    # SDK-0309 — standalone offline AI System passport verification
    "verify_ai_system_passport",
    # SEC-2026-09-12 MCPSDK-04 — pin the key fetch_and_verify must trust.
    "jwk_thumbprint",
    "jwk_thumbprint_hex",
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
    # SDK-0301 — advisory in-runtime interaction hooks
    "PraesidiaInteractionHooks",
    "AsyncPraesidiaInteractionHooks",
    "InteractionHookResult",
    "InteractionDecision",
    "InteractionOutcomeReceipt",
    "INTERACTION_OUTCOME_STATUSES",
    "INTERACTION_TYPES",
    "INTERACTION_VERDICTS",
    "DEFAULT_FAIL_MODES",
    "InteractionDeniedError",
    "InteractionDecisionUnavailableError",
]

from .protected_http import ProtectedHttpResource, verify_http_receipt, verify_protected_http_result
