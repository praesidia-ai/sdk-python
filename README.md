# praesidia — Python SDK

Python management SDK for the [Praesidia](https://praesidia.ai) AI agent platform.

Covers agents, agent-tasks, workflows, connections, audit log, analytics, EU AI
Act compliance reports, agent memory, OTLP GenAI telemetry emit, and offline
trust-passport verification via the Praesidia REST API.

## Installation

```bash
pip install praesidia
```

Requires Python 3.9+ and [`httpx`](https://www.python-httpx.org/) (installed automatically).

## Quick start

```python
from praesidia import Praesidia

client = Praesidia(
    api_key="sk-...",        # from Praesidia dashboard → Settings → API Keys
    org_id="your-org-id",   # organisation UUID
    base_url="https://api.praesidia.ai",  # default; omit for hosted API
    timeout=30.0,             # per-operation request timeout; max 300 seconds
)

# List agents
agents = client.agents.list()
print(f"Found {len(agents)} agents")

# Submit an agent task. POST /tasks binds CreateAgentTaskDto, which REQUIRES a
# connection UUID + a task type + a non-empty input object.
task = client.agents.run(
    "connection-uuid",                    # CreateAgentTaskDto.connectionId (UUID)
    input={"message": "Hello, agent!"},   # non-empty input object
    type="MESSAGE",                       # MESSAGE (default) | TOOL_CALL | DELEGATION
)
print(f"Task created: {task['id']}, status: {task['status']}")

# Trigger a workflow
run = client.workflows.trigger("workflow-id", input={"key": "value"})
print(f"Workflow run: {run['id']}")

# Stream the audit log
for event in client.audit.stream(from_date="2026-01-01"):
    print(event["action"], event["createdAt"])

# Cost trends
trends = client.analytics.cost_trends(days=30)

# Generate + download an EU AI Act compliance report (async job)
report = client.compliance.generate_and_wait()          # request + poll until ready
doc = client.compliance.get_json(report["reportId"])    # structured JSON (q1-04-v1)
pdf = client.compliance.get_pdf(report["reportId"])     # raw PDF bytes
with open("eu-ai-act-report.pdf", "wb") as fh:
    fh.write(pdf)
```

## Resources

| Resource | Class | Key methods |
|----------|-------|-------------|
| `client.agents` | `AgentsResource` | `list`, `list_page`, `list_all`, `get`, `create`, `update`, `delete`, `run`, `poll_pending_tasks`, `call_mcp_tool`, `protect_action`, `refresh_credential` |
| `client.workflows` | `WorkflowsResource` | `list`, `list_page`, `list_all`, `get`, `create`, `update`, `delete`, `trigger`, `list_runs`, `list_runs_page`, `list_runs_all`, `get_run` |
| `client.audit` | `AuditResource` | `list`, `stream`, `export` |
| `client.analytics` | `AnalyticsResource` | `usage`, `cost_trends`, `agent_performance`, `top_agents`, `export`, `capture_state`, `agent_analytics`, `events`, `activity_log`, `record_event`, `security_metrics`, `usage_heatmap`, `compliance_metrics`, `anomalies`, `cost_by_team`, `model_comparison` |
| `client.connections` | `ConnectionsResource` | `list`, `list_page`, `list_all`, `get`, `create`, `create_agent`, `create_mcp`, `update_status`, `delete`, `test`, `health` |
| `client.compliance` | `ComplianceResource` | `request_report`, `get_status`, `get_json`, `get_pdf`, `wait_for_report`, `generate_and_wait` |
| `client.memory` | `MemoryResource` | `create`, `list`, `search`, `erase`, `get`, `delete` |
| `client.telemetry` | `TelemetryResource` | `emit`, `emit_gen_ai_span`, `emit_gen_ai_spans`, `build_gen_ai_resource_spans` |
| `client.trust` | `TrustResource` | `fetch_passport`, `fetch_verify_bundle`, `verify_passport`, `fetch_and_verify` |

## Agent memory (H2-06e)

Org-scoped agent-memory store. Writes are PII-redacted + poisoning-scanned and
encrypted per-org on the backend; reads carry provenance + guardrail metadata.

```python
m = client.memory.create(
    content="The customer prefers email.",
    subject_id="user-42",   # binds the memory for a later GDPR Art-17 erase
    tags=["crm"],
)
hits = client.memory.search("contact preference", top_k=5)
rows = client.memory.list(limit=20, tag="crm")
client.memory.erase("user-42", reason="GDPR Art-17 request")
client.memory.delete(m["id"])
```

## OTLP GenAI telemetry — become an OBSERVED agent (H1-02)

Emit OTLP/HTTP GenAI-convention traces to `POST /telemetry/otlp/v1/traces`; the
backend materialises the emitting agent as an **observed** agent — no
registration. This is a **minimal, dependency-free emitter** (the SDK's only
runtime dependency is `httpx`); if you already run the OpenTelemetry SDK, point
its OTLP/HTTP exporter at the endpoint with an
`Authorization: Bearer <org pk_ key>` header instead.

```python
client.telemetry.emit_gen_ai_span(
    agent_name="support-bot",
    system="openai",
    request_model="gpt-4o",
    input_tokens=812,
    output_tokens=143,
)
# → {"accepted": True, "buffered": 1}
```

Bounds mirror the server (≤100 resourceSpans, ≤2 MB body, 120 req/min). Auth is
an ORGANIZATION API key; the endpoint accepts it as `Authorization: Bearer`
(what the client sends) or `X-API-Key`.

## Authentication (AUDIT-SDK-04)

The Python SDK sends the API key as an **`Authorization: Bearer <key>`** header on
every request — matching the TypeScript SDK, the CLI, and the backend's canonical
`ApiKeyStrategy`. This is why `memory`, `compliance`, `audit`, `analytics`,
`connections` and `workflows` all authenticate with a personal `pk_` key that
holds the required org role (the route-level guards were opened to API keys in
be-core commit `0bff5a0a`), and why routes fronted by `OrAuthGuard` /
passport-`api-key` (e.g. guardrails) — which read the credential **only** from
`Authorization: Bearer` — work too. (The prior `X-API-Key`-only header 401'd on
those `OrAuthGuard` routes.)

## Trust passport — verify a peer agent offline (H3-02f)

Fetch a peer agent's signed trust passport from the **public** trust routes and
verify its detached Ed25519 or KMS-backed P-256/ES256 proof **locally**, without
an online verification call. Offline verification is pure-Python and
**dependency-free** (compact Ed25519 + ECDSA-P256 verification and canonical
JSON in `praesidia._crypto`), so it needs no `cryptography` install.

The supplied JWK is the verification trust anchor. Resolve it from a trusted
DID document or verification bundle; a signature proves integrity relative to
that key, but cannot by itself prove that an arbitrary key belongs to the
passport's claimed issuer.

```python
result = client.trust.fetch_and_verify(peer_agent_id)
if result["verified"] and result["passport"]["credentialSubject"]["trustScore"] >= 70:
    ...  # signed reputation is genuine and fresh — trust the peer

# Or verify a passport handed to you out-of-band — no client / account needed:
from praesidia import verify_passport
result = verify_passport(passport, public_key_jwk)
# result["reason"] ∈ ok | missing-proof | malformed-public-key
#                    | malformed-passport | signature-mismatch
#                    | invalid-expiration | expired
```

## Agent credential refresh

Adopt a newly provisioned management API key at runtime for a
**zero-downtime** swap — no restart, no recreating the client.

```python
# Adopt a freshly provisioned secret in-process without recreating the client:
client.refresh_credential(new_management_api_key)
# (also available as client.agents.refresh_credential(...))
```

Subsequent management requests from this client authenticate with the new key.
For A2A task polling, pass either `access_token=` or `client_secret=` directly to
`poll_pending_tasks`; static secrets must use the `X-A2A-*` headers and must not
be mixed with a management Bearer credential.

## Chain trace + JIT capability tokens (Q3-02 / Q4-02)

Praesidia correlates a multi-agent call chain with an **unsigned**
`X-Praesidia-Chain-Id` header, and gates task-scoped MCP tool calls with a
short-lived **JIT capability token**.

```python
# Forward the inbound chain id (never mint one) so downstream hops stay joined:
task = client.agents.run(
    "connection-uuid", input={"message": "hi"}, chain_id=inbound_chain_id
)
# run() scopes the chain header to this request, so concurrent root tasks cannot
# inherit it. For a dedicated client whose every call belongs to the same chain:
client.forward_chain(inbound_chain_id)

# Poll the tasks routed to a server agent; each row now carries chainId,
# hopIndex and (when governance is on) an opaque capabilityToken (may be absent).
for row in client.agents.poll_pending_tasks(
    "client-id", access_token=agent_oauth_access_token
):
    # Execute an MCP tool call on behalf of the task, forwarding the four
    # task-binding fields (capabilityToken, taskId, agentId, chainId) as
    # X-Praesidia-* headers. The capability token is opaque — never log it.
    result = client.agents.call_mcp_tool(
        server_id="mcp-server-id",
        tool_name="search",
        arguments={"q": "quarterly filings"},
        task=row,                               # lifts the four fields
    )
```

`create()` returns `credentialMode` (`"jit"` | `"static"`) and `clientSecret`
(`str | None`). For JIT-first orgs `clientSecret` is `None` — do **not** persist
a static `X-A2A-Client-Secret`; use the JIT capability-token flow above.

## Protect a dispatch — `protect_action` (PA01 DX-002)

`call_mcp_tool` above never raises on denial and has no way to distinguish "the tool ran and
failed" from "the call was never authorized". `protect_action` is a **blocking, raising** wrapper
over the same managed MCP route — the one place `be`'s Proof Edge mints/binds/consumes a Permit
and durably records dispatch evidence **before** the call returns (evidence grade **C** at
best — Praesidia-managed observation, never independent target proof). It ignores no config knob:
there is no way to silently degrade it into best-effort behaviour like `call_mcp_tool`.

```python
from praesidia import ProtectedActionDeniedError, UnsupportedProtectedActionTargetError

try:
    result = client.agents.protect_action(
        server_id="mcp-server-id",
        tool_name="send_email",
        arguments={"to": "user@example.com", "subject": "Hi"},
    )
    # result["success"] / result["content"] — the tool's own dispatch outcome.
    # result.get("actionId") / .get("closure") / .get("evidenceGrade") are
    # None when the Proof Edge feature is off for the org; absence != failure.
except ProtectedActionDeniedError as e:
    # Permit missing/expired/invalid/mismatched, or a confirmed replay (which
    # denies even under observe-mode). e.action_deny_reason is the
    # machine-readable reason ("PERMIT_MISSING" | "PERMIT_INVALID" |
    # "PERMIT_EXPIRED" | "PERMIT_MISMATCH" | "PERMIT_REPLAYED" |
    # "POLICY_DENIED"); e.error_code / e.action_id / e.closure are also set.
    ...
except UnsupportedProtectedActionTargetError:
    # protocol was not "mcp" — the only destination this SDK version can
    # honestly protect. A customer-controlled Proof Edge for arbitrary
    # destinations (EDGE-003) is a later release; this NEVER silently falls
    # back to call_mcp_tool-style unraised behaviour.
    ...
```

Only `protocol="mcp"` (the default) is supported today. A tool-level failure (the call dispatched
and the *tool itself* reported an error, OR the call failed downstream with a transport/tool
exception) does **not** raise — it comes back as `result["isError"]` with `result["success"] is
False`; only a *pre-dispatch* denial (RBAC/ABAC gate, or the Proof Edge's Permit deny/mismatch/
replay) raises.

**PA-0026 — the discriminator is `actionDenyReason`, not `errorCode`.** `protect_action` raises
`ProtectedActionDeniedError` if and only if `be`'s response carries `actionDenyReason` — set on and
only on a genuine pre-dispatch denial. A downstream tool/transport exception returns `errorCode:
"BAD_REQUEST" | "INTERNAL_ERROR"` (no `actionDenyReason`) and a successful call whose tool errored
carries no `errorCode` at all; neither raises. (An earlier version of this SDK keyed the decision on
`errorCode != "TOOL_ERROR"`, which is wrong — `"TOOL_ERROR"` is never present in this endpoint's
caller-visible response.)

The Permit (D3) rides `X-Praesidia-Permit` — a header kept strictly
separate from the JIT `X-Praesidia-Capability-Token` verify path; PA01 has no HTTP permit-issuance
endpoint yet, so `permit=` is forward-compatible plumbing, not something you can obtain today.

Python has no `beginTask`/`TaskHandle`-style lifecycle object (unlike the TS SDK) — `protect_action`
is net-new on `AgentsResource`, matching the TS SDK's states, headers and error taxonomy exactly.

## Retry (FINDING-4) — bounded, idempotency-safe by default

The client retries **only** requests that are safe to repeat: GET, DELETE, and
any POST/PATCH the caller explicitly marks with an `idempotency_key`. **A bare
POST (task submission, agent/workflow/connection creation) is never
retried** — retrying an already-applied create/charge is a duplication bug,
not a resilience feature.

**R-SDK-1 — `idempotency_key` is allow-listed, not a blanket promise.**
be-core only deduplicates a request server-side on `Idempotency-Key` for
three routes today: `POST /organizations/:orgId/tasks`, `POST /a2a/tasks`,
and `POST /a2a/tasks/:taskId/result`. Every other route — including every
PATCH — ignores the header entirely. Passing `idempotency_key` to
`HttpClient.post`/`.patch` for any other path raises `ValueError`
immediately (no request is sent) rather than silently retrying a write the
server can double-apply.

Retries use jittered exponential backoff, honour a `Retry-After` header on
`429`/`5xx`, and are bounded by both an attempt count and a wall-clock budget:

```python
from praesidia import Praesidia, RetryConfig

client = Praesidia(
    api_key="sk-...",
    org_id="...",
    retry=RetryConfig(
        max_attempts=3,      # default: 3 (i.e. up to 2 retries)
        base_delay_s=0.25,   # default: 0.25
        max_delay_s=4.0,     # default: 4.0
        max_elapsed_s=15.0,  # default: 15.0 -- total budget across all attempts
    ),
)

# Disable retries entirely:
client_no_retry = Praesidia(api_key="sk-...", org_id="...", retry=False)

# Opt an idempotent write into retry:
client.agents.update("agent-1", {"name": "Renamed"})  # PATCH — not retried by default
```

To retry a POST/PATCH from a resource method, pass through to
`client._http.post(..., idempotency_key=...)` / `.patch(...)` directly — and
only for the three allow-listed routes above (in practice: task
submission) — or wait for a resource-level `idempotency_key` parameter (not
yet threaded through every resource method — the transport-level primitive
is what this release adds).

## Pagination (SCAN2-011)

`client.agents.list`, `client.connections.list`, `client.workflows.list`, and
`client.workflows.list_runs` return only the requested page as a bare list — their exact prior
signature, kept for backwards compatibility. There is no way to tell from that list alone whether
more rows exist beyond the page. Two additive methods exist alongside each for callers who need to
know:

- **`<method>_page(...)`** — same request, but returns be's full pagination envelope:
  `{"data": [...], "total": N, "meta": {"page", "limit", "total", "totalPages", "hasNextPage", "hasPrevPage"}}`.
- **`<method>_all(...)`** — a generator that auto-paginates through every page and yields every
  row, so "give me all of them" is correct by default:

```python
# First page only, exactly as before:
first_page = client.agents.list()

# Full envelope, so you can tell if there's more:
page = client.agents.list_page()
if page["meta"]["hasNextPage"]:
    ...

# Every agent, across every page:
for agent in client.agents.list_all():
    print(agent["id"])
```

## Error handling

All SDK errors derive from `PraesidiaError`:

```python
from praesidia import Praesidia, AuthError, NotFoundError, RateLimitError

client = Praesidia(api_key="sk-...", org_id="...")

try:
    agent = client.agents.get("nonexistent-id")
except NotFoundError:
    print("Agent not found")
except AuthError:
    print("Invalid or expired API key")
except RateLimitError:
    print("Too many requests — slow down")
```

`ProtectedActionDeniedError` and `UnsupportedProtectedActionTargetError` (PA01 DX-002) are raised
only by `client.agents.protect_action` — see [above](#protect-a-dispatch--protect_action-pa01-dx-002).

### be's structured error envelope (SCAN2-007)

Any non-2xx response from the Praesidia API raises one of the typed exceptions above. `.message`/
`.status_code` keep their original meaning for backwards compatibility, and every exception also
exposes be's structured error envelope as typed attributes so you don't have to string-match
`.message`:

```python
try:
    client.agents.list()
except PraesidiaError as err:
    print(err.status_code)   # HTTP status, e.g. 429
    print(err.code)          # be's machine error code, e.g. "RATE_LIMITED" (may be None)
    print(err.request_id)    # for support correlation (may be None)
    print(err.details)       # validation/field errors, shape varies by route (may be None)
    print(err.retry_after)   # seconds to wait on a 429/503, if be sent one (may be None)
    print(err.retryable)     # True for 429/5xx -- whether retrying is worth it at all
    print(err.body)          # the full raw parsed envelope, or None if the body wasn't JSON
```

`code`/`request_id`/`details`/`retry_after`/`body` are `None` whenever be's response wasn't a JSON
object (e.g. an intermediary proxy's plain-text error) — never assume they are populated.

## Local development

```bash
# Point at a local BE instance
client = Praesidia(
    api_key="sk-local-key",
    org_id="org-uuid",
    base_url="http://localhost:5001",
)
```

The local BE exposes the OpenAPI spec at `http://localhost:5001/api-docs-json`.

## Contributing

Reproducible dev environment via `uv` (this repo's committed `uv.lock` is the
source of truth):

```bash
uv sync --extra dev
uv run pytest -q
```

Without `uv`, a plain venv works too:

```bash
pip install -e ".[dev]"
pytest
```

### API contract drift check (CD-0007)

`praesidia/*.py` hand-writes be-core's REST routes and request-body shapes
(a `self._base` f-string precomputed per resource class, then interpolated
or passed straight through to `self._http.get/post/put/patch/delete/
stream_get`). This is checked against a fresh be-core OpenAPI spec by the
**same** contract-drift scanner the TypeScript SDK uses
(`sdk/scripts/audit-api-contract.mjs --lang py` — see that file's header),
not a separate Python-native re-derivation, so this SDK never drifts from
its own gate's design. Run locally via a sibling checkout of `sdk`:

```bash
node ../sdk/scripts/audit-api-contract.mjs <path-to-swagger.json> \
  --source praesidia --lang py
```

Wired as its own CI job (`.github/workflows/contract-drift.yml`): sibling
checkouts of `sdk` (owns the scanner), `be-core` (spec source of truth) and
`queue-core`, mirroring `mcp`'s (CD-0001) and `sdk`'s (CD-0006) equivalent
jobs.

## Changelog

### Unreleased — AUD-0063: close the analytics resource coverage gap

- **Added** 11 `AnalyticsResource` methods closing be-core's remaining
  `/organizations/{org_id}/analytics*` routes: `capture_state`,
  `agent_analytics`, `events`, `activity_log`, `record_event`,
  `security_metrics`, `usage_heatmap`, `compliance_metrics`, `anomalies`,
  `cost_by_team`, `model_comparison`. `AnalyticsResource` previously covered
  5 of be-core's 15 analytics paths; it now covers all of them. Purely
  additive — no existing method signature changed.
- **Added** a swagger.json-derived coverage test
  (`tests/test_analytics_coverage.py`, mirrored in the TypeScript SDK) that
  fails on any `/organizations/{org_id}/analytics*` operation this resource
  does not implement, so a future be-added route is caught here instead of
  silently missing the SDK.
- `record_event` is a bare, never-retried POST (not in be-core's
  `Idempotency-Key` allowlist) and requires `ANALYTICS_CREATE` — no mintable
  API-key scope exists for it, so it needs a JWT bearer. Every other new
  method is an idempotent GET, retried per the existing policy.

### Unreleased — production contract hardening

- **Fixed** compliance report generation validating its polling options only
  after enqueueing a report; invalid options now fail before any network side
  effect.
- **Fixed** agent-memory enum drift against the backend and fail fast on
  invalid content, search limits, and retention settings that the backend
  would reject or silently ignore. Retention days now require the `CUSTOM`
  regime.
- **Hardened** retry, workflow-budget, chain-header, analytics, audit-export,
  and OTLP telemetry validation against non-finite, unsafe, or backend-invalid
  values.
- **Fixed** isolated release builds producing Core Metadata 2.5 artifacts that
  Twine 6.2 cannot validate; the backend and release tools are pinned and the
  wheel/sdist now emit publishable Metadata-Version 2.4.
- **Expanded** behavioral coverage across every public resource method,
  authentication mode, legacy response envelope, and typed HTTP error mapping.

### Unreleased — PA-0026: fix `protect_action`'s deny discriminator (defect in PA01 DX-002)

- **Fixed** `protect_action` misclassifying a downstream tool/transport error as a pre-dispatch
  policy denial. The shipped heuristic (`errorCode != 'TOOL_ERROR'`) was broken: `'TOOL_ERROR'` is
  never present in this endpoint's caller-visible response, so both a real tool exception
  (`errorCode: 'BAD_REQUEST' | 'INTERNAL_ERROR'`) and a successful call whose tool errored (no
  `errorCode` at all) satisfied the old "raise" condition. Switched the discriminator to presence
  of the response's `actionDenyReason` field, which `be` sets on and only on genuine pre-dispatch
  denials.
- **Added** `ProtectedActionDeniedError.action_deny_reason` (one of `"PERMIT_MISSING"` |
  `"PERMIT_INVALID"` | `"PERMIT_EXPIRED"` | `"PERMIT_MISMATCH"` | `"PERMIT_REPLAYED"` |
  `"POLICY_DENIED"`).
- No breaking change to `protect_action`'s return shape; `ProtectedActionDeniedError` gained an
  additive attribute.

### Unreleased — PA01 DX-002: `AgentsResource.protect_action` (blocking/raising Proof Edge wrapper)

- **Added** `client.agents.protect_action(...)` — a blocking, raising wrapper over the managed MCP
  Proof Edge (`POST /organizations/{org_id}/mcp-servers/{server_id}/tools/{tool_name}/call`).
  Raises `ProtectedActionDeniedError` on a pre-dispatch denial (missing/expired/invalid/mismatched
  Permit, or a confirmed replay) and `UnsupportedProtectedActionTargetError` for any `protocol`
  other than `"mcp"` — never silently downgrades to `call_mcp_tool`-style unraised behaviour.
- **Added** `jcs_canonicalize`/`jcs_commitment`/`JcsCanonicalizationError` (RFC 8785 JCS) —
  byte-compared against the shared `sdk`/`be`/`audit-verifier` golden fixtures.

### 0.3.1 — R-SDK-1: allow-list the routes `idempotency_key` may retry

- **Fixed** `idempotency_key` retry is now allow-listed to the routes
  be-core actually deduplicates (`POST /organizations/:orgId/tasks`,
  `POST /a2a/tasks`, `POST /a2a/tasks/:taskId/result`); every other path
  raises `ValueError` instead of retrying a write the server can
  double-apply. Previously any path accepted the option. **Behavioral,
  non-breaking for existing callers** — no shipped resource method passed
  `idempotency_key` before this fix, so no caller's request shape changes;
  the transport-level escape hatch is simply narrower/safer than before.

### 0.3.0 — bounded retry (FINDING-4) + dev environment

- **Added** bounded, idempotency-safe retry to `HttpClient` (`get`/`delete`
  retry by default; `post`/`patch` only retry when called with
  `idempotency_key=...`). New `retry` constructor kwarg on `Praesidia`
  (`RetryConfig` instance, `None` for the default policy, or `False` to
  disable). Non-breaking — no existing method signature changed.
- **Dev environment**: this repo's `uv.lock` is the source of truth for a
  reproducible dev environment — `uv sync --extra dev && uv run pytest -q`
  runs the full suite without touching the ambient system Python. (`pip
  install -e ".[dev]"` remains a valid alternative for contributors without
  `uv`.)

### 0.2.0 — audit / download bug-fix wave

- **BREAKING — `audit.list()` drops the `resource_type` argument.** The
  backend `FilterAuditDto` never accepted a `resourceType` query param and
  rejects unknown params (`forbidNonWhitelisted`), so *every* call that
  passed `resource_type` got a hard `400` for the whole request — it never
  worked, so no functioning caller can break. `resourceType` is a value the
  backend *derives* from the `action` prefix at read time, not a stored,
  queryable column. **Filter by `action` instead** (e.g.
  `client.audit.list(action="agent.created")`). *(BUGHUNT-SDK-03)*
- **`audit.stream(limit=...)` now streams the full range for any `limit`.**
  The backend hard-caps a page at 100 rows (`PAGINATION_MAX_LIMIT`); the old
  "stop when a page is shorter than `limit`" heuristic treated the first
  clamped page as the last and silently dropped every row past the first 100
  whenever `limit > 100`. The iterator now advances until the server returns
  an empty page, so a `stream(limit=500)` over 5,000 rows yields all 5,000.
  *(BUGHUNT-SDK-01)*
- **Bulk downloads no longer hang forever on a stalled server.**
  `compliance.get_pdf`, `audit.export` and `analytics.export` used
  `timeout=None`, which disabled *all* httpx timeouts (connect/read/write/
  pool). They now use a finite per-operation budget (`connect=10s`,
  `read=60s` idle-between-chunks, `write=30s`, `pool=10s`) that still lets a
  large-but-progressing download finish, plus an optional `timeout=` override.
  *(BUGHUNT-SDK-06)*
- **Offline passport canonical-JSON matches be-core for astral object keys.**
  `praesidia._crypto.canonical_json` now sorts object keys by their UTF-16
  code-unit sequence (matching V8 / `Array.prototype.sort`), so a passport
  with dynamic non-BMP keys reconstructs the same signing preimage be-core
  signed instead of falsely failing verification. *(BUGHUNT-SDK-04)*

## Versioning

SDK version tracks the Praesidia API version; a minor bump before 1.0 may also
carry an SDK-level breaking change (see the Changelog above). See `CHANGELOG.md`
in the repo root.
