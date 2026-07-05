# praesidia — Python SDK

Python management SDK for the [Praesidia](https://praesidia.ai) AI agent platform.

Covers agents, agent-tasks, workflows, connections, audit log, analytics, and
EU AI Act compliance reports via the Praesidia REST API.

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
| `client.agents` | `AgentsResource` | `list`, `get`, `create`, `update`, `delete`, `run`, `rotate_client_secret`, `refresh_credential` |
| `client.workflows` | `WorkflowsResource` | `list`, `get`, `create`, `update`, `delete`, `trigger`, `list_runs`, `get_run` |
| `client.audit` | `AuditResource` | `list`, `stream`, `export` |
| `client.analytics` | `AnalyticsResource` | `usage`, `cost_trends`, `agent_performance`, `top_agents`, `export` |
| `client.connections` | `ConnectionsResource` | `list`, `get`, `create`, `create_agent`, `create_mcp`, `update_status`, `delete`, `test`, `health` |
| `client.compliance` | `ComplianceResource` | `request_report`, `get_status`, `get_json`, `get_pdf`, `wait_for_report`, `generate_and_wait` |

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
