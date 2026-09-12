# `praesidia` (Python SDK) — architecture

Module map with `path:line` anchors, verified against the current tree 2026-09-12.

## `praesidia/` — one resource module per concern, re-exported from `__init__.py`

```
praesidia/client.py            # class Praesidia (client.py:41) — the composed client, holds
                                # one resource attribute per concern (agents, workflows, ...)
praesidia/_http.py              # HttpClient — the shared httpx-based transport every resource
                                # module's f-string routes go through
praesidia/guard.py               # class TaskHandle (guard.py:103), class Guard (guard.py:164) —
                                # convenience wrapper + offline local-rules guardrail fallback
                                # (TOP-0008)
praesidia/local_rules.py         # run_local_rules — offline (no API call) guardrail evaluation
praesidia/agents.py               # class AgentsResource (agents.py:66); tool_call_headers_from_task
praesidia/workflows.py            # WorkflowsResource
praesidia/connections.py          # ConnectionsResource
praesidia/audit.py                # AuditResource
praesidia/analytics.py            # AnalyticsResource
praesidia/compliance.py           # ComplianceResource — EU AI Act report export
praesidia/memory.py               # MemoryResource
praesidia/telemetry.py            # gen_ai_span — OTLP GenAI span emission
praesidia/trust.py                # verify_passport — offline trust-passport verification
praesidia/identity.py             # IdentityClient, IdentityError
praesidia/proof.py                # class ProofResource (proof.py:16) — protected-action evidence
praesidia/protected_http.py       # ProtectedHttpResource, verify_http_receipt,
                                  # verify_protected_http_result
praesidia/_crypto.py              # signing/verification primitives (proof/identity/http-receipt)
praesidia/_jcs_canonical.py       # RFC 8785 JSON Canonicalization used for signed payloads
praesidia/_pagination.py          # list_page/list_all pagination helpers (SCAN2-011 parity)
praesidia/_retry.py               # RetryConfig — bounded, idempotency-safe retry
praesidia/exceptions.py           # PraesidiaError + structured error envelope (SCAN2-007 parity)
```

`praesidia/__init__.py:31-103` is the public export surface — every symbol above that a consumer
is meant to import directly is re-exported there; internal modules are prefixed `_` by
convention (`_http.py`, `_crypto.py`, `_jcs_canonical.py`, `_pagination.py`, `_retry.py`).

## `praesidia/integrations/` — optional framework adapters

```
crewai.py                      # CrewAI adapter
openai_agents.py                # OpenAI Agents Python adapter
google_adk.py                   # Google ADK adapter
microsoft_agent_framework.py    # Microsoft Agent Framework Python adapter
agno.py                          # Agno adapter
langgraph.py / langgraph_tools.py  # LangGraph adapter + tool wrappers
protected_tool.py                # shared protected-action tool-wrapping logic
attempt_store.py                 # local attempt/checkpoint persistence (parity with
                                  # sdk's FileRuntimeAttemptStore)
```

Per `sdk-python/README.md:24-28`: these adapters use the authoritative protected-HTTP
prepare/checkpoint/resume API; they do not implicitly intercept a runtime's unrelated effects.

## `plugins/hermes` — separate local package

A Nous Hermes Agent managed tool with an explicit strict-tool profile
(`sdk-python/README.md:26-27`). Packaged and versioned independently of the root `praesidia`
package.

## Contract-drift gate (CD-0007)

`praesidia/*.py` hand-writes `be`'s REST routes/request-body shapes. Checked against a fresh `be`
OpenAPI spec by the **same** scanner the TypeScript SDK uses
(`sdk/scripts/audit-api-contract.mjs --lang py`), not a separate Python-native re-derivation
(`sdk-python/README.md:578-591`). Wired as its own CI job
(`.github/workflows/contract-drift.yml`) alongside sibling checkouts of `sdk` and `be-core`.

## PyPI packaging note

`pyproject.toml:58` sets `Documentation = "https://docs.praesidia.ai/sdk/python"` — that hostname
does not currently resolve (checked live 2026-09-12, `docs/README.md`'s "Publishing status"
section). This affects the PyPI project page's metadata once published, not this SDK's runtime
behavior.
