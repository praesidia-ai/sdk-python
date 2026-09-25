# praesidia — Python SDK

Python management SDK for the [Praesidia](https://praesidia.ai) AI agent platform.

Covers agents, agent-tasks, workflows, connections, audit log, analytics, EU AI
Act compliance reports, agent memory, OTLP GenAI telemetry emit, offline
trust-passport verification and advisory in-runtime interaction hooks via the
Praesidia REST API.

## Installation

```bash
pip install praesidia
```

This README describes the current source checkout. A registry release may not
contain every method shown here. For unreleased features, use the matching
reviewed wheel supplied by your deployment operator, or build this checkout
with `python -m build` and install the resulting local `.whl` file.
A successful local build does not publish a PyPI release.

Requires Python 3.9+ and [`httpx`](https://www.python-httpx.org/) (installed automatically).

## Managed runtime tools (0.4.1 source)

Optional native adapters now cover CrewAI, OpenAI Agents Python, Google ADK,
Microsoft Agent Framework Python, Agno and LangGraph. A separate local package
adds a Nous Hermes Agent managed tool and explicit strict-tool profile. Effects
use the authoritative protected HTTP prepare/checkpoint/resume API; unrelated
runtime effects are not implicitly intercepted. See [runtime integration profiles,
versions, restart CLI and evidence boundaries](docs/runtime-integrations.md).

## Organization runtime installations

Create an installation in the app’s **Agent runtimes** setup and complete its
one-use challenge from the host with a personal or delegated user credential.
Set `PRAESIDIA_RUNTIME_INSTALLATION_ID`, or pass `runtime_installation_id` to
`Praesidia`. The SDK commits this ID into protected HTTP checkpoints and durable
managed-tool state. Conflicting explicit installation IDs fail before HTTP.
Disabling an installation fences new bound execution; authenticated checkpoint
readback remains available. Connection state records credential possession at
verification time, rather than host attestation or unrestricted runtime coverage.

## Quick start

```python
from praesidia import Praesidia

client = Praesidia(
    api_key="pk_...",        # personal management key from Profile → API Keys
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

Responses are read through bounded streams: JSON responses are limited to
16 MiB, error bodies to 64 KiB, and bulk downloads to 128 MiB. A response that
exceeds its limit raises `ResponseTooLargeError` before it can grow without
bound in memory.

## Resources

| Resource | Class | Key methods |
|----------|-------|-------------|
| `client.agents` | `AgentsResource` | `list`, `get`, `create`, `update`, `delete`, `run`, `poll_pending_tasks`, `call_mcp_tool`, `protect_action`, `refresh_credential` |
| `client.ai_systems` | `AiSystemsResource` | `list`/`get`/`create`/`update`/`archive`/`restore`/`delete`/`update_owners`/`transition_lifecycle`/`request_lifecycle_transition`/`approve_lifecycle_transition`/`reject_lifecycle_transition`/`retire`/`reapprove`/`summary`, `list_assets`/`create_asset`/`get_asset`/`update_asset`/`archive_asset`/`restore_asset`/`adopt_asset`, `attach_asset`/`detach_asset`/`change_asset_role`, `create_relationship`/`get_relationship`/`update_relationship`/`archive_relationship`/`restore_relationship`/`list_relationships`/`traverse`, `put_{system,asset,relationship}_by_external_id`/`delete_{system,asset,relationship}_by_external_id` (each `list*` also has a `*_page`/`*_all` sibling) |
| `client.workflows` | `WorkflowsResource` | `list`, `get`, `create`, `update`, `delete`, `trigger`, `list_runs`, `get_run` |
| `client.audit` | `AuditResource` | `list`, `stream`, `export`, `export_bundle` |
| `client.proof` | `ProofResource` | `list`, `get`, `events`, `capture_scope`, `coverage_summary` |
| `client.analytics` | `AnalyticsResource` | `usage`, `cost_trends`, `agent_performance`, `top_agents`, `export`, `capture_state`, `agent_analytics`, `events`, `activity_log`, `record_event`, `security_metrics`, `usage_heatmap`, `compliance_metrics`, `anomalies`, `cost_by_team`, `model_comparison` |
| `client.connections` | `ConnectionsResource` | `list`, `get`, `create`, `create_agent`, `create_mcp`, `update_status`, `delete`, `test`, `health` |
| `client.compliance` | `ComplianceResource` | `request_report`, `get_status`, `get_json`, `get_pdf`, `wait_for_report`, `generate_and_wait` |
| `client.memory` | `MemoryResource` | `create`, `list`, `search`, `erase`, `get`, `delete` |
| `client.telemetry` | `TelemetryResource` | `emit`, `emit_gen_ai_span`, `emit_gen_ai_spans`, `build_gen_ai_resource_spans` |
| `client.trust` (or keyless `PraesidiaTrust()`) | `TrustResource` | `fetch_passport`, `fetch_verify_bundle`, `verify_passport`, `fetch_and_verify`, `fetch_ai_system_passport`, `fetch_ai_system_verify_bundle`, `fetch_ai_system_badge_svg`, `fetch_ai_system_passport_pdf`, `verify_ai_system_passport`, `fetch_and_verify_ai_system` |

## AI Systems / assets / relationship graph (SDK-0002/SDK-0004, parity with be's AISYS-0002 and `sdk`'s SDK-0001/SDK-0003)

`client.ai_systems` manages the AI System inventory, the AI Asset catalog (agents, models, MCP
servers, data sources, ...), the membership linking assets to systems, and the relationship graph
(edges) between assets.

```python
from praesidia import Praesidia

client = Praesidia(api_key="sk-...", org_id="...")
system = client.ai_systems.create({"name": "Support triage bot", "criticality": "high"})
asset = client.ai_systems.adopt_asset({"entityType": "agent", "entityId": agent_id, "aiSystemId": system["id"]})
client.ai_systems.attach_asset(system["id"], {"assetId": asset["id"], "role": "primary"})
client.ai_systems.create_relationship({
    "sourceAssetId": asset["id"],
    "targetAssetId": other_asset_id,
    "relationshipType": "CALLS",
})
```

| Method | Returns | Endpoint |
|---|---|---|
| `list(**filters)` | `list[dict]` | `GET .../ai-systems` |
| `get(ai_system_id)` | `dict` | `GET .../ai-systems/:id` |
| `create(data)` | `dict` | `POST .../ai-systems` |
| `update(ai_system_id, data)` | `dict` | `PATCH .../ai-systems/:id` |
| `update_owners(ai_system_id, data)` | `dict` | `PATCH .../ai-systems/:id/owners` |
| `transition_lifecycle(ai_system_id, lifecycle_status)` | `dict` | `PATCH .../ai-systems/:id/lifecycle` (ungated targets only) |
| `request_lifecycle_transition(ai_system_id, to_status, *, reason=None)` | `dict` | `POST .../ai-systems/:id/lifecycle-requests` |
| `approve_lifecycle_transition(request_id, *, reason=None)` | `dict` | `POST .../ai-systems/lifecycle-requests/:requestId/approve` |
| `reject_lifecycle_transition(request_id, *, reason=None)` | `dict` | `POST .../ai-systems/lifecycle-requests/:requestId/reject` |
| `retire(ai_system_id, *, retention_policy, reason, retention_until=None)` | `dict` | `POST .../ai-systems/:id/retire` (202) |
| `reapprove(ai_system_id, material_change_id, *, reason=None)` | `dict` | `POST .../ai-systems/:id/reapprove` |
| `archive(ai_system_id)` | `dict` | `POST .../ai-systems/:id/archive` |
| `restore(ai_system_id)` | `dict` | `POST .../ai-systems/:id/restore` |
| `delete(ai_system_id)` | `None` | `DELETE .../ai-systems/:id` (soft-delete) |
| `summary(ai_system_id)` | `dict` | `GET .../ai-systems/:id/summary` |
| `list_assets(**filters)` | `list[dict]` | `GET .../ai-assets` |
| `create_asset(data)` | `dict` | `POST .../ai-assets` |
| `get_asset(asset_id)` | `dict` | `GET .../ai-assets/:id` |
| `update_asset(asset_id, data)` | `dict` | `PATCH .../ai-assets/:id` |
| `archive_asset(asset_id)` | `dict` | `POST .../ai-assets/:id/archive` |
| `restore_asset(asset_id)` | `dict` | `POST .../ai-assets/:id/restore` |
| `adopt_asset(data)` | `dict` | `POST .../ai-assets/adopt` (idempotent) |
| `attach_asset(ai_system_id, data)` | `dict` | `POST .../ai-systems/:id/assets` |
| `change_asset_role(ai_system_id, asset_id, role)` | `dict` | `PATCH .../ai-systems/:id/assets/:assetId/role` |
| `detach_asset(ai_system_id, asset_id)` | `None` | `DELETE .../ai-systems/:id/assets/:assetId` |
| `create_relationship(data)` | `dict` | `POST .../asset-relationships` |
| `get_relationship(relationship_id)` | `dict` | `GET .../asset-relationships/:id` |
| `update_relationship(relationship_id, data)` | `dict` | `PATCH .../asset-relationships/:id` |
| `archive_relationship(relationship_id)` | `dict` | `POST .../asset-relationships/:id/archive` |
| `restore_relationship(relationship_id)` | `dict` | `POST .../asset-relationships/:id/restore` |
| `list_relationships(**filters)` | `list[dict]` | `GET .../asset-relationships` |
| `traverse(asset_id, **filters)` | `dict` | `GET .../asset-relationships/graph/traverse` |
| `put_system_by_external_id(external_id, data)` | `dict` | `PUT .../ai-systems/by-external-id/:externalId` |
| `delete_system_by_external_id(external_id)` | `dict` | `DELETE .../ai-systems/by-external-id/:externalId` (archives) |
| `put_asset_by_external_id(external_id, data)` | `dict` | `PUT .../ai-assets/by-external-id/:externalId` |
| `delete_asset_by_external_id(external_id)` | `dict` | `DELETE .../ai-assets/by-external-id/:externalId` (archives) |
| `put_relationship_by_external_id(external_id, data)` | `dict` | `PUT .../asset-relationships/by-external-id/:externalId` |
| `delete_relationship_by_external_id(external_id)` | `dict` | `DELETE .../asset-relationships/by-external-id/:externalId` (archives) |

`production` and `retired` are approval-gated (be AISYS-0018): be answers a direct
`PATCH .../lifecycle` into either with 400, so `transition_lifecycle` raises
`PraesidiaConfigError` before sending. File a request, then an `ORGANIZATION_OWNER` other than
the requester approves it; the approval is what applies the move:

```python
req = client.ai_systems.request_lifecycle_transition(system_id, "production", reason="Passed review")
# as an ORGANIZATION_OWNER (not the requester):
client.ai_systems.approve_lifecycle_transition(req["id"], reason="Approved")  # or reject_lifecycle_transition

# Retirement has its own door; it records the retention policy and returns 202 {requestId, preview}
ret = client.ai_systems.retire(
    system_id,
    retention_policy="Audit evidence retained 7 years, then destroyed.",
    reason="Superseded by the v3 model.",
)
client.ai_systems.approve_lifecycle_transition(ret["requestId"])  # applies retirement + cascade
```

`request_lifecycle_transition` raises `ValueError` for `"retired"` (use `retire`). `reapprove`
clears the re-approval flag a material change left on a `production` system.

Every `list*`/`list_assets`/`list_relationships` also has a `*_page` (full pagination envelope) and
`*_all` (auto-paginating generator) sibling, matching the `list_page`/`list_all` convention above
(SCAN2-011). List filters are keyword-only and validated client-side against be's enums
(`ValueError` on an unknown value); `include_archived` is sent as the lowercase string
`"true"`/`"false"` since be's DTOs declare it `@IsBooleanString`, not a real boolean.
`transition_lifecycle`'s `lifecycle_status` is validated the same way; `change_asset_role`'s `role`
is not (matches `attach_asset`'s `role`, per `AiSystemAssetRole` being hand-copied rather than
derived from the entity's `as const` array — be 400s on an unknown value).
`create_asset` and `put_asset_by_external_id` validate `assetType`/`discoveryStatus` the same way,
and `source` against `CLIENT_ASSET_SOURCES` (`"manual"`/`"api"`/`"import"`). The other
`ASSET_SOURCES` values are written only by be's own pipelines (BE-1529). The
`list_assets*` `source` filter still accepts all of `ASSET_SOURCES`.

`traverse(asset_id, **filters)` walks the relationship graph from an anchor asset (`direction`
one of `"downstream"`/`"upstream"`/`"both"`, client-side validated like the list filters above;
`asset_types`/`relationship_types` are now also client-side validated against
`ASSET_TYPES`/`RELATIONSHIP_TYPES` (SDK-0007 — a test asserts those tuples match
`ui/swagger.json`'s enums, so a stale-constant false rejection would fail CI before shipping);
`max_depth`/`include_archived` are passed through unvalidated). `include_archived`
uses the same boolean-string encoding as the list filters. `summary(ai_system_id)` returns a thin
per-section aggregation (`compliance`/`risk`/`evaluations`/`cost`/`evidence`, each
`{"available": bool, "reason"?, "counts"?, "updatedAt"?}`) plus `unlinkedAssets`; a section's
`available: false` means be cannot filter that section by this system's asset ids at all yet, not
that the count is zero.

`put_{system,asset,relationship}_by_external_id(external_id, data)` (be's BE-0579,
SDK-0302/PRAE-228/229) declaratively create-or-update a row keyed by an externally-owned
`external_id` — the shape IaC tooling (Terraform provider, k8s operator) needs instead of a
lookup-then-create/update round trip. Each returns
`{"id", "externalId", "created", "changed", "updatedAt", "resource"}` — `changed` is the
plan-stability signal: the same `data` sent twice returns `changed: False` the second time with
an unchanged `updatedAt`; nothing was written. `delete_{system,asset,relationship}_by_external_id`
archives (never a hard delete) and returns the same shape. Another tenant's `external_id` 404s on
the DELETE rather than leaking existence; every lookup is org-scoped.

## Guard — guardrail checks + audit logging, with an offline fallback (TOP-0008)

`Guard` is a batteries-included convenience wrapper — the Python equivalent of `sdk`'s
(TypeScript) `PraesidiaGuard` — for the common "check input, run my agent, check output, log an
audit task" loop. It works with **zero configuration**: with no API key it runs bundled
rule-based guardrail patterns locally, covering prompt injection, PII (SSN / credit-card-shaped
digit runs), hate speech, and violence/threats, with **zero network calls**.

```python
from praesidia import Guard

guard = Guard()  # no env vars -> local/offline mode
result = guard.run(lambda: call_my_llm(prompt), input=prompt)
print(result["output"])
```

### Modes

- **Local mode (no account needed)** — when `PRAESIDIA_API_KEY`/`PRAESIDIA_ORG_ID` are not set
  (or not passed to the constructor), every check runs
  [`praesidia.local_rules.run_local_rules`](praesidia/local_rules.py) synchronously, with zero API
  calls. `CheckResult["local"]` is `True`.
- **Connected mode (free Praesidia account)** — set `PRAESIDIA_API_KEY` + `PRAESIDIA_ORG_ID` (and
  optionally `PRAESIDIA_AGENT_ID` / `PRAESIDIA_CONNECTION_ID` / `PRAESIDIA_BASE_URL`, or pass them
  as constructor kwargs) to check content against `POST /organizations/:org_id/guardrails/validate`
  and persist audit tasks. `PRAESIDIA_CONNECTION_ID` (a `CreateAgentTaskDto`-required UUID) is
  needed for `run`/`log_task`/`begin_task` to actually persist a task; without one the audit
  submit is skipped (or raises in `strict` mode) — it never silently 400s.

### `Guard(...)`

```python
guard = Guard(
    api_key="pk_...",           # falls back to PRAESIDIA_API_KEY
    org_id="org-uuid",          # falls back to PRAESIDIA_ORG_ID
    agent_id="agent-uuid",      # falls back to PRAESIDIA_AGENT_ID
    connection_id="conn-uuid",  # falls back to PRAESIDIA_CONNECTION_ID; required to persist audit tasks
    base_url="https://api.praesidia.ai",  # falls back to PRAESIDIA_BASE_URL
    strict=False,     # True -> re-raise network errors (default: degrade gracefully)
    fail_open=False,  # True -> silently swallow network errors (default: warn + local fallback)
)
```

### `guard.run(fn, *, input, ...)` and `@guard.protect(...)` — the idiomatic decorator form

`run()` is a direct behavioural port of `sdk`'s `guard.run(fn, opts)`: it checks input (raising
`GuardrailBlockedError` and never calling `fn` on a block), calls `fn()`, checks output, and
records one best-effort audit task, returning
`{"output", "taskId", "inputCheck", "outputCheck"}`.

`protect()` is the Python-idiomatic alternative — a **decorator** rather than a callback closure,
since Python can forward a wrapped function's own arguments where TS's callback shape cannot:

```python
@guard.protect(task_type="chat")
def call_llm(prompt: str) -> str:
    return openai_call(prompt)

reply = call_llm("hello")  # guarded input/output + audited transparently
```

### `guard.check_input(content, ...)` / `guard.check_output(content, ...)` → `CheckResult` dict

Standalone checks without running a function. `guard_input`/`guard_output` wrap these and raise
`GuardrailBlockedError` on a block (`guard_output` only by default when `strict=True`, or pass
`throw_on_block=True`).

### `guard.begin_task(...)` → `TaskHandle` — also a context manager

Direct port of `beginTask`/`TaskHandle.complete`/`.fail` (memoized: the first `complete`/`fail`
call wins, later calls replay the same outcome without a second write). Idiomatic-Python addition
over the TS shape: `TaskHandle` doubles as a context manager, so an exception raised inside the
`with` block calls `fail()` automatically instead of requiring a manual `try`/`except`/`finally`:

```python
with guard.begin_task(input=prompt, task_type="chat") as task:
    output = call_my_llm(prompt)
    task.complete(output)
# an exception here calls task.fail(exc) once, then re-raises
```

### `guard.log_task(task)` / `guard.forward_chain(chain_id)`

Manual audit-task logging and chain-trace propagation — same semantics as `sdk`'s
`guard.logTask`/`guard.forwardChain`.

### Not on `Guard` — use `Praesidia` directly

`Guard` intentionally does **not** re-expose `protect_action` or `refresh_credential`: they
already have tested homes on `Praesidia.agents.protect_action` (see
[Protect a dispatch](#protect-a-dispatch--protect_action-pa01-dx-002) below) and
`Praesidia.refresh_credential` — duplicating them on `Guard` would just be two ways to reach the
same code with no behavioural difference. This is an intentional, documented surface difference
from `sdk`'s single `PraesidiaGuard` class, which owns every management primitive on one object.
`sdk`'s `guard.trackToolCall` (grade-D best-effort tool-call telemetry) has **no Python SDK
equivalent at all** yet — a pre-existing gap independent of this feature, not addressed here.

## Inspect protected actions and export signed evidence

Create a **separate review client** with a personal, user-backed `pk_` key
carrying `audit:read`. Protected-action reads require the user's
`protected_actions.view` permission and the workspace's `proof.actions`
feature. Organization, service-account, and application keys do not qualify;
runtime access to a tool does not grant organization-wide evidence access.

```python
import os
from pathlib import Path
from praesidia import Praesidia

review = Praesidia(
    api_key=os.environ["PRAESIDIA_REVIEW_API_KEY"],
    org_id=os.environ["PRAESIDIA_ORG_ID"],
)
# Use the actionId returned by agents.protect_action(), or proof.list().
action_id = os.environ["PRAESIDIA_ACTION_ID"]
action = review.proof.get(action_id)
events = review.proof.events(action_id)
print(action["closure"], action["verificationStatus"], len(events))
scope = review.proof.capture_scope()
coverage = review.proof.coverage_summary()
page = review.proof.list(closure="OUTCOME_UNKNOWN", limit=20)

# Also requires owner/compliance-officer role and COMPLIANCE_VIEW permission.
bundle = review.audit.export_bundle(
    from_date="2026-09-01T00:00:00Z", to_date="2026-09-02T00:00:00Z",
)
Path("audit-bundle.zip").write_bytes(bundle)
```

`proof.list()` accepts `agent_id`, `task_id`, `chain_id`, `state`, `closure`,
`from_date`, `to_date`, `page`, and `limit` (1–100), preserving the full
`data`/`total`/`meta` response. The lower time bound is inclusive and the upper
bound exclusive. `events()` preserves decimal sequence strings, signatures,
and redacted `None` payloads without coercing or interpreting them.

`audit.export_bundle()` downloads a signed ZIP; `audit.export()` still exports
ordinary JSON/CSV logs. Signed windows must be greater than zero and at most
90 days. Dates are `YYYY-MM-DD` (UTC) or explicit-timezone ISO timestamps with
up to three fractional-second digits. The existing finite transport timeout
and 128 MiB download cap apply; oversized downloads raise `ResponseTooLargeError`.

A successful read or download **is not verification**. `SUCCEEDED` describes
an operational outcome and can coexist with incomplete evidence. The
projection's `evidenceGrade` is server-declared. Run the obtained offline
verifier with an independently trusted deployment platform key, inspect all
component results, and retain evidence gaps. This management API is separate
from native MCP OAuth, A2A bindings, or standardized SCITT receipts.

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
# Files a two-person GDPR Art-17 request; nothing is destroyed yet.
approval = client.memory.erase("user-42", reason="GDPR Art-17 request")
assert approval["status"] == "PENDING"  # 202 ApprovalRequest
client.memory.delete(m["id"])
```

`erase(subject_id, reason, expected_subject_hash=None, acknowledge_cross_org=None)`
returns the pending `DATA_SUBJECT_ERASE` ApprovalRequest the API answers with (202),
not a shred result. The crypto-shred and its erasure certificate only happen when a
different system admin confirms the approval. `expected_subject_hash` is optional:
if you omit it, the server derives it from `subject_id`. If you pass it, it must be 64
lowercase hex characters (checked locally before the request) and must match the
server's HMAC, otherwise the API returns 400 `subject_hash_mismatch`. Pass
`acknowledge_cross_org=True` when the subject is a platform user who also belongs to
other organisations.

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

Generated spans pin OpenTelemetry semantic conventions **1.37.0** and emit
`gen_ai.provider.name`. Set `traceparent` to a valid W3C parent and use
`task_id` / `action_id` for correlation. Trace metadata does not authorize an
action or prove its execution.

Content capture is off by default. `capture_content=True` requires an explicit
`redact_content` callback; secret attributes remain excluded. Raw `emit` is an
explicit pass-through API, so apply your exporter privacy policy first.
Backend ingestion retains only bounded metadata before queueing. Run the real
collector acceptance from the sibling infra repository with
`node scripts/verify-telemetry-interoperability.mjs` after building the TypeScript
SDK and preparing this repository's `.venv`.

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

**No Praesidia account needed.** Every trust route is public, so a third-party
verifier builds the client with no API key and no org (SDK-0311; TS parity:
`new PraesidiaTrust()`). `PraesidiaTrust` has every `client.trust` method shown
below and never sends an `Authorization` header:

```python
from praesidia import PraesidiaTrust

trust = PraesidiaTrust()  # optional: base_url= (else PRAESIDIA_BASE_URL), timeout=, retry=
result = trust.fetch_and_verify_ai_system(ai_system_id, trusted_keys=[issuer_jwk])
pdf = trust.fetch_ai_system_passport_pdf(ai_system_id)
```

An authenticated `Praesidia(api_key=..., org_id=...)` keeps `client.trust` unchanged.

The supplied JWK is the verification trust anchor. Resolve it from a trusted
DID document or verification bundle; a signature proves integrity relative to
that key, but cannot by itself prove that an arbitrary key belongs to the
passport's claimed issuer.

**`fetch_and_verify` requires a trust anchor.** The verify route is public and
unauthenticated and returns the passport *and* the key, so checking one against
the other proves nothing — anyone able to answer that request can mint both.
Pass the key (or its fingerprint) that you obtained some other way:

```python
result = client.trust.fetch_and_verify(
    peer_agent_id,
    trusted_keys=[issuer_jwk],        # sequence, or a mapping keyed by kid/issuer
    # expected_fingerprint="sha256:…" # alternative: pin the RFC 7638 thumbprint
)
if result["verified"] and result["passport"]["credentialSubject"]["trustScore"] >= 70:
    ...  # signed reputation is genuine and fresh — trust the peer

# With NO anchor the signature is still checked, but the call refuses to call
# the outcome an assurance:
unpinned = client.trust.fetch_and_verify(peer_agent_id)
# {"verified": False, "reason": "unpinned_key", "signatureValid": True, ...}

# Print the thumbprint of a key you trust, to pin it elsewhere:
from praesidia import jwk_thumbprint, jwk_thumbprint_hex
jwk_thumbprint(issuer_jwk)  # base64url; jwk_thumbprint_hex() for hex

# Or verify a passport handed to you out-of-band — no client / account needed:
from praesidia import verify_passport
result = verify_passport(passport, my_trusted_jwk)
# result["reason"] ∈ ok | missing-proof | malformed-public-key
#                    | malformed-passport | signature-mismatch
#                    | invalid-expiration | expired
#                    | unpinned_key | untrusted_key | fingerprint_mismatch
```

| `fetch_and_verify` anchor | Outcome |
|---|---|
| none | `verified: False`, `reason: "unpinned_key"`, `signatureValid` truthful |
| `trusted_keys` contains the signing key | `verified: True` (subject to expiry) |
| `trusted_keys` without the signing key | `verified: False`, `reason: "untrusted_key"` |
| `expected_fingerprint` matches the served key | verified normally against that key |
| `expected_fingerprint` differs | `verified: False`, `reason: "fingerprint_mismatch"` |

A human-readable PDF of an **AI System's** signed passport (signature
fingerprint + verification URL printed on it) is a public download too:

```python
pdf = client.trust.fetch_ai_system_passport_pdf(ai_system_id)  # bytes, starts with b"%PDF-"
open("trust-passport.pdf", "wb").write(pdf)
# Unpublished (passportVisibility PRIVATE, the default), unknown or
# soft-deleted AI System → NotFoundError
```

The rest of the **AI System** passport routes are public as well — `TrustResource`
never sends the API key on any of them:

```python
passport = client.trust.fetch_ai_system_passport(ai_system_id)
# dict (be's AiSystemTrustPassportDto): credentialSubject.{aiSystemName, frameworks,
# attestations, posture, redTeam, regulatoryClassification, aibom, dataCategories,
# incidents, models, permissions, evidenceRoot} — each section is
# {"available": True, "counts": {...}} or {"available": False, "reason": ...}
# (a gap is never reported as a zero count).

bundle = client.trust.fetch_ai_system_verify_bundle(ai_system_id)
# {"passport", "publicKeyJwk", "verificationHint",
#  "embed": {"badgeUrl", "verifyUrl", "html", "markdown"}}

svg = client.trust.fetch_ai_system_badge_svg(ai_system_id)  # str, "<svg …>"
# Unpublished (passportVisibility PRIVATE, the default), unknown or
# soft-deleted AI System → NotFoundError; the verify bundle raises a
# retryable ServerError (503) when be cannot load the org signing key.
```

| Method | Route | Auth |
|---|---|---|
| `fetch_ai_system_passport` | `GET /trust/passport/ai-systems/{ai_system_id}` | public (no auth) |
| `fetch_ai_system_verify_bundle` | `GET /trust/passport/ai-systems/{ai_system_id}/verify` | public (no auth) |
| `fetch_and_verify_ai_system` | `GET /trust/passport/ai-systems/{ai_system_id}/verify` + offline verify | public (no auth) |
| `fetch_ai_system_badge_svg` | `GET /trust/passport/ai-systems/{ai_system_id}/badge.svg` | public (no auth) |
| `fetch_ai_system_passport_pdf` | `GET /trust/passport/ai-systems/{ai_system_id}/passport.pdf` | public (no auth) |

These routes serve a passport only once its owner publishes it: every AI
System starts with `passportVisibility` `PRIVATE` (existing systems included),
and an unpublished one raises the same `NotFoundError` as an id that does not
exist. An org member with `ai_systems.update` publishes or withdraws it with
`PATCH /organizations/{org_id}/ai-systems/{id}` — from this SDK, the API-keyed
`client.ai_systems.update(ai_system_id, {"passportVisibility": "PUBLIC"})`,
not `client.trust`.

The bundle's `publicKeyJwk` comes from the same unauthenticated response as the
passport, so it is not a trust anchor on its own. Verify an AI System passport
offline with `verify_ai_system_passport` / `fetch_and_verify_ai_system` — same
proof, signature, expiry and trust-anchor rules as the agent functions above (be
signs both passports through one path):

```python
result = client.trust.fetch_and_verify_ai_system(
    ai_system_id, trusted_keys=[issuer_jwk_from_your_did_document]  # or expected_fingerprint=
)
# No anchor → {"verified": False, "reason": "unpinned_key", "signatureValid": True, ...}

from praesidia import verify_ai_system_passport
offline = verify_ai_system_passport(passport_handed_to_you, my_trusted_jwk)
```

The two envelopes are not interchangeable: `verify_passport` returns
`malformed-passport` for an AI System passport and `verify_ai_system_passport`
returns it for an agent passport. The AI System check also enforces be's section
contract — a gap (`"available": False`) carries a `reason` and never `counts`.

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

## Gateway — tag calls with an MCP server id (SDK-0313)

Point the OpenAI or Anthropic SDK at the Praesidia gateway, then use `gateway_headers` to name the
MCP server a call is made for. The gateway reports the egress it observes against that server,
removes the header before forwarding the call, and treats the id as untrusted: be records it only
for a server your key's org owns.

```python
from openai import OpenAI
from praesidia import gateway_headers

client = OpenAI(
    base_url="https://gateway.praesidia.ai/openai/v1",
    api_key="pra_...",
    default_headers=gateway_headers(mcp_server_id=SERVER_ID),       # per client
)
client.chat.completions.create(
    model="gpt-4o-mini", messages=[...],
    extra_headers=gateway_headers(mcp_server_id=OTHER_SERVER_ID),   # per call; wins
)
```

`gateway_headers()` with no id returns `{}`, so no header is sent. An id that is not one
hyphenated 8-4-4-4-12 hex UUID raises `InvalidMcpServerIdError` (a `PraesidiaConfigError`)
before any request, instead of the gateway's 400 `invalid_mcp_server_id`. Build both dicts with
`gateway_headers` (header name `MCP_SERVER_ID_HEADER`) so the per-call value replaces the client
value. If the two use different letter case, both are sent and the gateway rejects the duplicate.
The TypeScript SDK's twin (SDK-0312) wraps `fetch` instead. The Python OpenAI and Anthropic SDKs
accept these headers directly, so no custom HTTP client is needed.

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

## Interaction hooks — advisory in-runtime guard (SDK-0301)

Shell commands, code runs, file access, browser actions and tool calls run on **your** compute.
Praesidia does not operate or intercept that runtime. `PraesidiaInteractionHooks` is an
**advisory in-runtime guard**: before the action, your code asks Praesidia for a decision
and the SDK enforces that decision in your process. An agent that does not load the SDK, or
skips a hook, is not governed by it. For enforcement Praesidia sits in the path of, route the
action through a governed MCP server (`client.agents.protect_action`) instead.

```python
import os
import subprocess
from praesidia import PraesidiaInteractionHooks

def search_web(q: str) -> str: ...

with PraesidiaInteractionHooks(
    api_key=os.environ["PRAESIDIA_API_KEY"],    # org key with agents:invoke
    org_id=os.environ["PRAESIDIA_ORG_ID"],
    agent_id=os.environ["PRAESIDIA_AGENT_ID"],  # required: the agent tool policies decide
    timeout=5,  # httpx timeout in seconds, per phase, not per request (see Fail mode)
) as hooks:
    hooks.before_exec("git status", cwd="/srv/repo")  # raises on deny
    subprocess.run(["git", "status"], cwd="/srv/repo")

    search = hooks.guarded(search_web)  # before_tool_call("search_web", kwargs) on every call
    search(q="praesidia")
```

`AsyncPraesidiaInteractionHooks` (over `httpx.AsyncClient`) has the same constructor and methods;
every hook is awaited (`await hooks.before_exec(...)`), `guarded` returns an async wrapper that
accepts a sync or async tool, and it closes with `await hooks.aclose()` or `async with`.

| Hook | Asks as | Default on outage |
|---|---|---|
| `before_tool_call(tool_name, arguments=None)` | `model_to_tool.<tool_name>` | fail-open |
| `before_exec(command, args=None, cwd=None, runtime="shell")` | `agent_to_shell.exec` (`runtime="code"` → `agent_to_code_execution.exec`) | **fail-closed** |
| `before_fs_access(path, mode)` | `agent_to_filesystem.<mode>` | fail-open for `read` / `list`, **fail-closed** for `write` / `delete` |
| `before_browser_action(action, url=None, arguments=None)` | `agent_to_browser.<action>` | fail-open |
| `before_interaction(type, {"name", "arguments"?}, *, fail_mode="closed")` | `<type>.<name>`, any of `INTERACTION_TYPES` | **fail-closed** |

Every hook returns `InteractionHookResult(decision=...)` on `allow`, raises
`InteractionDeniedError` on `deny`, and on `require_approval` blocks: it re-asks every
`approval_poll_interval` seconds (default 2), echoing `approvalId`, until a human approves
(returns) or rejects / the approval expires (raises). After `approval_timeout` seconds (default
600) it raises with `reason_code="approval_wait_timeout"`. `on_approval_required(decision)` is
called once when the wait starts, so you can tell someone which approval to act on.
`guarded(tool, tool_name=None)` wraps a keyword-argument tool (the kwargs become `arguments`;
the name defaults to `tool.__name__`) and keeps its signature for framework introspection.
The TS SDK has no `guarded` equivalent: call `beforeToolCall` before the tool there.

**Fail mode.** An outage is a network error, a timeout, a 408 / 429 / 5xx, or a malformed
response. `timeout` (seconds, default 30) is httpx's: it bounds the connect, the request write,
the wait for a pooled connection and each read of the response separately, not the whole
request, so a hook can wait longer than `timeout` before its fail mode applies. A fail-closed
hook then raises `InteractionDecisionUnavailableError` (the outage is its
`__cause__`); a fail-open hook returns `InteractionHookResult(decision=None, fail_open_error=err)`.
Any other 4xx (bad key, unknown agent, feature not enabled) always raises the typed
`PraesidiaError` (`AuthError`, `ForbiddenError`, ...), on every hook. The defaults fail closed
where a skipped check can do irreversible local damage with no other Praesidia control in the
path (shell / code execution, filesystem writes), and fail open for read-only and lower-impact
checks so a Praesidia outage does not stop every agent. Override per class with
`fail_mode={"tool_call" | "exec" | "fs_read" | "fs_write" | "browser": "open" | "closed"}`. An
outage while waiting for an approval never turns into an allow: the hook keeps waiting, then
times out.

**Cache.** A verdict is reused for its `ttlSeconds` for the identical request, in memory, per
hooks instance (at most 1000 entries). be sends 30, or 0 (never reused) when the answer came
from a rule that requires approval (including the `allow` of a consumed approval, and the `allow`
that `observe` mode gives in place of an approval), from a daily-limited rule's `allow`, or from
a failed policy evaluation (`reasonCode` `policy_service_error`, a deny in `enforce` mode). Cached
verdicts are valid only under the `policyFingerprint` that produced them: a response with a new
fingerprint evicts them all. A policy change therefore takes effect within `ttlSeconds`.

In `observe` governance mode be answers `allow` and records the would-be decision; in `off` it
answers `allow`. `decide(type, action, approval_id=None)` is the raw call (no cache, no wait, no
fail mode). Action names must be dot-separated `[A-Za-z0-9_-]` segments (be's rule); anything
else raises `PraesidiaConfigError` before a request is sent. The four typed hooks and `guarded`
omit an argument whose value is `None` (and `arguments` when none is left);
`before_interaction` and `decide` send `action` as given, so a `None` value is sent as JSON
`null`. The route is `POST /organizations/{orgId}/interaction-decisions` (`agents:invoke` key
scope, `AGENT_POLICIES` feature); the request and response bytes match the TS SDK's, proven by
replaying be's recorded `test-fixtures/interaction-decision-v1.json` in both SDKs.

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

`InteractionDeniedError` (`interaction_type`, `action_name`, `reason_code`, `decision`) and
`InteractionDecisionUnavailableError` (`__cause__` = the outage) are raised only by the interaction
hooks — see [Interaction hooks](#interaction-hooks--advisory-in-runtime-guard-sdk-0301).

## Not covered by this SDK

The following `be` API surfaces have no client methods here, intentionally — they are
org-admin / dashboard configuration screens consumed by the Praesidia UI, not primitives an
agent-runtime caller needs:

- **`governance-controls`** (catalog/create/patch/review/runs) — the governance-policy admin
  catalog.
- **`mcp-servers/inventory`** (list/refresh/review/dependencies) — the MCP tool-inventory review
  surface.
- **`runtime-installations`** management (create/patch/challenge/disable/list/verify) — only the
  opaque, already-provisioned `runtime_installation_id` is accepted (see
  [Organization runtime installations](#organization-runtime-installations) above); creating and
  verifying an installation is done once, in the app.
- **`identity`** provider/binding/consent/grant/revocation CRUD — only `IdentityClient`'s
  `exchange` / `down_exchange` / `introspect` (token-exchange and introspection) are covered.
- **`agents/oauth/browser`** admin endpoints (authorize/approve/deny/browser-client CRUD) — only
  the token-exchange side effect is consumed, via `identity` above.

This matches `sdk`'s (TypeScript) coverage exactly (no TS↔Python gap). If any of these should
become SDK-callable, treat it as a new feature request, not a bug in this list.

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

### Unreleased — SDK-0318: asset writes send only client sources (BE-1529)

- **Fixed** `create_asset` / `put_asset_by_external_id` now raise `ValueError` on a `source`
  outside the new `AiSystemsResource.CLIENT_ASSET_SOURCES` (`manual`/`api`/`import`), before
  sending. Previously `discovery_connector`/`runtime_observation`/`entitlement_projection`
  reached be and got a 400. `create_asset` now also validates `assetType`/`discoveryStatus`,
  as `put_asset_by_external_id` already did.
- **Fixed** `ASSET_SOURCES` (5 → 6: adds `entitlement_projection`). Before this,
  `list_assets*(source="entitlement_projection")` raised `ValueError` even though be's list
  filter accepts it.
- **Behaviour change (semver patch)**: an invalid create/put value now raises `ValueError`
  instead of the server's 400 `PraesidiaError`. Any value that now raises was already
  rejected by be ≥ BE-1529. TS↔Python parity: mirrors `sdk`'s SDK-0317.

### Unreleased — SDK-0301: interaction hooks, an advisory in-runtime guard (BE-1486)

- **Added** `PraesidiaInteractionHooks` (httpx.Client) and `AsyncPraesidiaInteractionHooks`
  (httpx.AsyncClient): `before_tool_call`, `before_exec`, `before_fs_access`,
  `before_browser_action`, `before_interaction`, `decide`, `guarded`, plus `INTERACTION_TYPES`,
  `INTERACTION_VERDICTS`, `DEFAULT_FAIL_MODES`, `InteractionHookResult`, `InteractionDecision`
  (`praesidia/interaction_hooks.py`) and `InteractionDeniedError` /
  `InteractionDecisionUnavailableError` (`praesidia/exceptions.py`). Calls be's
  `POST /organizations/{orgId}/interaction-decisions`; replays be's recorded fixture
  `test-fixtures/interaction-decision-v1.json` (the same file the TS SDK replays). TS↔Python
  parity: mirrors `sdk`'s SDK-0300 (same defaults, verdicts, fail modes, cache and approval wait);
  `guarded` is Python-only. Additive only, no breaking change (semver minor).

### Unreleased — SDK-0315: `ASSET_TYPES` adds `GUARDRAIL`

- **Fixed** `AiSystemsResource.ASSET_TYPES` (23 → 24: adds `GUARDRAIL`) to match
  `ui/swagger.json`'s `AiAsset.assetType` enum (be BE-0338). Before this, `list_assets*`,
  `put_asset_by_external_id` and `traverse` raised `ValueError` on `"GUARDRAIL"` before sending
  the request. `RELATIONSHIP_TYPES` re-checked against the same swagger: already
  in sync (13 values). TS↔Python parity: mirrors `sdk`'s SDK-0314. No breaking changes — widened
  valid-value set only.

### Unreleased — SDK-0311: `PraesidiaTrust`, the trust routes without credentials

- **Added** `PraesidiaTrust(base_url=None, *, timeout=30.0, retry=None)`
  (`praesidia/trust.py`, exported from `praesidia`): a `TrustResource` that
  needs no `api_key` / `org_id`, so a verifier with no Praesidia account can call
  every trust fetch (`fetch_ai_system_passport_pdf`, `fetch_and_verify_ai_system`,
  …) without placeholder credentials. `base_url` falls back to `PRAESIDIA_BASE_URL`,
  then `https://api.praesidia.ai`. TS parity: `new PraesidiaTrust()`. No breaking
  changes — `Praesidia(...)` and `client.trust` are unchanged.

### Unreleased — SDK-0310: public AI System passport routes (BE-0540)

- **Added** to `TrustResource` (`praesidia/trust.py`): `fetch_ai_system_passport`,
  `fetch_ai_system_verify_bundle` (both `dict`, shaped like be's
  `ai-system-trust-passport.dto.ts`) and `fetch_ai_system_badge_svg` (`str`) for
  `GET /trust/passport/ai-systems/{ai_system_id}[/verify|/badge.svg]` — public,
  sent without the API key, like `fetch_ai_system_passport_pdf`. TS parity:
  `PraesidiaTrust.fetchAiSystemPassport` / `fetchAiSystemVerifyBundle` /
  `fetchAiSystemBadgeSvg`. No breaking changes — additive only.

### Unreleased — SDK-0302: `by-external-id` desired-state methods (PRAE-228/229)

- **Added** to `AiSystemsResource` (`praesidia/ai_systems.py`): `put_system_by_external_id`/
  `delete_system_by_external_id`, `put_asset_by_external_id`/`delete_asset_by_external_id`,
  `put_relationship_by_external_id`/`delete_relationship_by_external_id` (be's BE-0579
  desired-state API) — the shape IaC tooling (Terraform provider PRAE-228, k8s operator
  PRAE-229) needs. Each returns `{"id", "externalId", "created", "changed", "updatedAt",
  "resource"}` (`DesiredStateOutcomeDto`); `changed` is the plan-stability signal, surfaced not
  swallowed. New `HttpClient.put`/`.delete_returning` (`_http.py`) back them — `put` is retried
  like `get`/`delete` (PUT is naturally idempotent, no `idempotency_key` needed) and
  `delete_returning` is `delete`'s sibling for a DELETE route that answers with a JSON body
  instead of 204. TS↔Python parity: mirrors `sdk`'s SDK-0302. No breaking changes — additive
  only.

### Unreleased — SDK-0304: `RELATIONSHIP_TYPES` adds `WRITES`

- **Fixed** `AiSystemsResource.RELATIONSHIP_TYPES` (12 → 13: adds `WRITES`) to match
  `ui/swagger.json`'s `AssetRelationship.relationshipType` enum (DB-0502, the write half of the
  PRAE-161 lineage chain). No breaking changes — widened valid-value set only.

### Unreleased — SDK-0007: `ASSET_TYPES`/`RELATIONSHIP_TYPES` contract sync

- **Fixed** `AiSystemsResource.ASSET_TYPES` (20 → 23: adds `TOOL`, `API_ENDPOINT`, `DATA_SCOPE`)
  and `.RELATIONSHIP_TYPES` (9 → 12: adds `CAN_INVOKE`, `GRANTS_SCOPE`, `CAN_ASSUME`) to match
  `ui/swagger.json`'s `AiAsset.assetType`/`AssetRelationship.relationshipType` enums (DB-0300).
  `traverse`'s `asset_types`/`relationship_types` filters are now client-side validated against
  the synced tuples (same `ValueError` shape as `direction`) — previously skipped because the
  constants lagged be's enum (SDK-0006). New test
  `test_asset_and_relationship_types_match_openapi` reads the sibling `ui/swagger.json` and fails
  if the tuples drift again. No breaking changes to signatures — widened valid-value sets and a
  new (additive) client-side check that only rejects values be already 400s on.

### Unreleased — SDK-0006: `traverse` (AISYS-0003) + `summary` (AISYS-0004)

- **Added** `AiSystemsResource.traverse(asset_id, **filters)`
  (`GET .../asset-relationships/graph/traverse`) and `.summary(ai_system_id)`
  (`GET .../ai-systems/:id/summary`, added opportunistically — same contract batch, cheap to
  cover in the same item) once both routes landed on `ui/swagger.json`. `traverse`'s `direction`
  is client-side validated (`"downstream"`/`"upstream"`/`"both"`); `asset_types`/
  `relationship_types` are not (see the note above the method table). No breaking changes —
  additive methods only, no existing signature touched.

### Unreleased — SDK-0004: full CONTRACT parity for the AI System / asset / relationship graph

- **Added** to `AiSystemsResource` (`praesidia/ai_systems.py`): AI System `update_owners`,
  `transition_lifecycle`, `delete`; AI Asset `create_asset`/`get_asset`/`update_asset`/
  `archive_asset`/`restore_asset`; membership `change_asset_role`; relationship
  `get_relationship`/`update_relationship`/`archive_relationship`/`restore_relationship`. Closes
  the SDK-0002 exclusions except multi-hop graph `traverse` (AISYS-0003 — landed in SDK-0006 once
  the route reached `ui/swagger.json`). See
  [AI Systems / assets / relationship graph](#ai-systems--assets--relationship-graph-sdk-0002sdk-0004-parity-with-bes-aisys-0002-and-sdks-sdk-0001sdk-0003)
  for the full method table. No breaking changes — additive methods only, no existing signature
  touched.

### Unreleased — SDK-0002: AI System / asset / relationship graph resource

- **Added** `AiSystemsResource` (`praesidia/ai_systems.py`), exposed as `client.ai_systems`,
  parity with `sdk`'s (TypeScript) `PraesidiaAiSystems` (SDK-0001) and be-core's AISYS-0002
  module: AI System CRUD (`list`/`get`/`create`/`update`/`archive`/`restore`), AI Asset
  read/adopt (`list_assets`/`adopt_asset`), AI System ↔ Asset membership
  (`attach_asset`/`detach_asset`), and asset relationship create/list
  (`create_relationship`/`list_relationships`). Every list method follows the SCAN2-011
  `list`/`*_page`/`*_all` convention. See
  [AI Systems / assets / relationship graph](#ai-systems--assets--relationship-graph-sdk-0002-parity-with-bes-aisys-0002-and-sdks-sdk-0001)
  for the exact method table and documented exclusions (unchanged from SDK-0001). No breaking
  changes — new resource only, no existing export or signature touched.

### Unreleased — TOP-0008: `Guard` convenience wrapper + offline local-rules guardrail fallback

- **Added** `praesidia.Guard` (`praesidia/guard.py`) and
  `praesidia.local_rules.run_local_rules` (`praesidia/local_rules.py`), the Python port of `sdk`'s
  `PraesidiaGuard`/`runLocalRules` (`sdk/src/guard.ts`, `sdk/src/local-rules.ts`). Closes the gap
  where Python integrators had `protected_http`/`integrations.protected_tool` but no convenience
  wrapper and no offline guardrail fallback — see [Guard](#guard--guardrail-checks--audit-logging-with-an-offline-fallback-top-0008)
  above for the full surface, including the Python-idiomatic `@guard.protect(...)` decorator and
  `TaskHandle` context-manager addition over the TS callback-closure shape.
- Local-rule patterns are compiled with `re.ASCII` — JS's `\d`/`\w`/`\b` are always ASCII-only
  regardless of flags, while Python's `re` module defaults to Unicode-aware; without `re.ASCII` a
  non-ASCII digit run (e.g. Eastern Arabic-Indic digits) would trip the SSN/credit-card patterns
  in Python but never in the TS SDK. See `tests/test_local_rules.py`'s parity table, verified
  against a live run of `sdk`'s compiled `runLocalRules`.
- Purely additive — no existing method signature changed.

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

### 0.4.1 — organization runtime installation binding (unpublished)

Adds explicit `runtime_installation_id` and `PRAESIDIA_RUNTIME_INSTALLATION_ID`
binding to protected HTTP checkpoints and managed-tool durable state. Conflicting
installation IDs fail before network access. The backend can now disable future
bound execution while preserving authenticated checkpoint readback.

### 0.4.0 — managed protected runtime tools (unpublished)

- Added reusable exact-request managed tools and six optional native framework
  adapters; native state is a recovery cursor, never approval authority.
- Added a separately installable Hermes plugin with native durable state,
  session/call binding and explicit default blocking of unrelated tools.
- Preserved pending/denied/expired/partial/unknown distinctions and once-only
  resume handling; no native effect callable or approval Boolean is accepted.
- Added actual framework tool, native approval serialization, and separate
  process restart tests plus a CLI for full-backend acceptance without model keys.
- Base dependencies and Python 3.9+ management compatibility remain unchanged;
  exact supported optional runtime versions are documented separately.

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

### Durable protected HTTP execution

`client.protected_http` exposes `prepare`, `checkpoint`, `resume`, `revoke` and `acknowledge`. Install `praesidia[langgraph]` on Python 3.10+ to use `praesidia.integrations.langgraph.protected_http_graph` with a durable checkpointer. The wake-up value never substitutes for a distinct human approval in Praesidia.

See `examples/protected_http_langgraph.py` for separate-process preparation and resume against a real backend, including independent target receipt verification and caller acknowledgment. The versioned wire contract is documented in the [TypeScript SDK protected HTTP guide](https://github.com/praesidia-ai/sdk/blob/main/docs/protected-http.md). Unknown outcomes must be inspected through checkpoint readback; resume is never transparently retried.
