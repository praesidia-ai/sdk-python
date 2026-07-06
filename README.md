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
)

# List agents
agents = client.agents.list()
print(f"Found {len(agents)} agents")

# Run an agent task
task = client.agents.run("agent-id", input={"message": "Hello, agent!"})
print(f"Task created: {task['id']}, status: {task['status']}")

# Trigger a workflow
run = client.workflows.trigger("workflow-id", input={"key": "value"})
print(f"Workflow run: {run['id']}")

# Stream the audit log
for event in client.audit.stream(from_date="2026-01-01"):
    print(event["action"], event["createdAt"])

# Cost trends
trends = client.analytics.cost_trends(period="30d")

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
| `client.agents` | `AgentsResource` | `list`, `get`, `create`, `update`, `delete`, `run`, `poll_pending_tasks`, `call_mcp_tool`, `rotate_client_secret`, `refresh_credential` |
| `client.workflows` | `WorkflowsResource` | `list`, `get`, `create`, `update`, `delete`, `trigger`, `list_runs`, `get_run` |
| `client.audit` | `AuditResource` | `list`, `stream`, `export` |
| `client.analytics` | `AnalyticsResource` | `usage`, `cost_trends`, `agent_performance`, `top_agents`, `export` |
| `client.connections` | `ConnectionsResource` | `list`, `get`, `create`, `create_agent`, `create_mcp`, `update_status`, `delete`, `test`, `health` |
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
an ORGANIZATION API key; the endpoint accepts it as `X-API-Key` (what the client
sends) or `Authorization: Bearer`.

## Trust passport — verify a peer agent offline (H3-02f)

Fetch a peer agent's signed trust passport from the **public** trust routes and
verify the detached Ed25519 proof **locally** — the "verify a peer's reputation
without trusting Praesidia" client. Offline verification is pure-Python and
**dependency-free** (a compact RFC 8032 Ed25519 verify + canonical JSON in
`praesidia._crypto`), so it needs no `cryptography` install.

```python
result = client.trust.fetch_and_verify(peer_agent_id)
if result["verified"] and result["passport"]["credentialSubject"]["trustScore"] >= 70:
    ...  # signed reputation is genuine and fresh — trust the peer

# Or verify a passport handed to you out-of-band — no client / account needed:
from praesidia import verify_passport
result = verify_passport(passport, public_key_jwk)
# result["reason"] ∈ ok | missing-proof | malformed-public-key
#                    | signature-mismatch | expired
```

## Agent client-secret rotation

Rotate an agent's A2A client secret and adopt the new one at runtime for a
**zero-downtime** swap. Requires the ``AGENTS_CONFIGURE`` permission.

```python
# Rotate with a 1-hour grace overlap so the OLD secret keeps working while
# consumers roll over. Omit grace_period_seconds (or pass 0) for an instant,
# fail-closed rotation (old secret dies immediately — the panic button).
rotated = client.agents.rotate_client_secret("agent-id", grace_period_seconds=3600)

# rotated["clientSecret"] is the NEW plaintext secret — shown ONCE. Store it
# now (it is never recoverable) and NEVER log it.
# rotated["graceEndsAt"] — ISO-8601 until which the previous secret also works (or None).

# Adopt the rotated secret in-process without recreating the client:
client.refresh_credential(rotated["clientSecret"])
# (also available as client.agents.refresh_credential(...))
```

`grace_period_seconds` is bounded 0..604800 (7 days) and clamped server-side;
the effective value is returned as `rotated["gracePeriodSeconds"]`.

**JIT-first orgs (Q4-05).** Organizations on the ephemeral/JIT-first default
have static client secrets disabled. `rotate_client_secret` then raises a clear
`ForbiddenError` (HTTP 403) — there is no static secret to rotate; the org
authenticates with ephemeral JIT capability tokens instead:

```python
from praesidia import ForbiddenError

try:
    client.agents.rotate_client_secret("agent-id")
except ForbiddenError as err:
    print(err)  # ...JIT-first...capability tokens...
```

## Chain trace + JIT capability tokens (Q3-02 / Q4-02)

Praesidia correlates a multi-agent call chain with an **unsigned**
`X-Praesidia-Chain-Id` header, and gates task-scoped MCP tool calls with a
short-lived **JIT capability token**.

```python
# Forward the inbound chain id (never mint one) so downstream hops stay joined:
client.forward_chain(inbound_chain_id)          # X-Praesidia-Chain-Id on every call
task = client.agents.run("agent-id", input={"message": "hi"}, chain_id=inbound_chain_id)

# Poll the tasks routed to a server agent; each row now carries chainId,
# hopIndex and (when governance is on) an opaque capabilityToken (may be absent).
for row in client.agents.poll_pending_tasks("client-id"):
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

```bash
pip install -e ".[dev]"
pytest
```

## Versioning

SDK version tracks the Praesidia API version.  See `CHANGELOG.md` in the repo root.
