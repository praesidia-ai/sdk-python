# Managed runtime tools — Python 0.4.1

Each adapter exposes one explicit managed tool. Its effect runs through the
backend's registered protected HTTP target and independent approval; there is no
local effect callback to accidentally execute while approval is pending.
This is not universal interception of a framework, agent, provider or MCP server.

Install the reviewed local 0.4.1 wheel with one optional extra, for example
`pip install 'praesidia[google-adk]==0.4.1'` after that version is published, or
`pip install -e '.[google-adk]'` from this checkout now. All six tested extras can
coexist in Python 3.11 using `.[frameworks]`; CrewAI's selected release does not
support Python 3.14. Base management SDK support remains Python 3.9+.

| Runtime | Tested package | Actual tool/context seam | Persisted identity/state |
| --- | --- | --- | --- |
| CrewAI | `crewai==1.15.20` | `BaseTool.run/_run` and a host binding from real Flow state | Flow `id`, stable host-issued logical step ID, `praesidia_tools` |
| OpenAI Agents Python | `openai-agents==0.20.0` | `FunctionTool`, native `ToolContext`, `needs_approval`, Runner interruptions | Native tool-call ID; host session ID/context plus serialized `RunState` |
| Google ADK | `google-adk==2.8.0` | `FunctionTool.run_async`, injected `ToolContext` | Native session ID/function-call ID, state delta `praesidia_tools` |
| Microsoft Agent Framework Python | `agent-framework-core==1.17.0` | Actual `FunctionTool.invoke` captures its separate native `tool_call_id` | `AgentSession.session_id/state`; ID is not model arguments |
| Agno | `agno==3.0.6` | Actual Function/FunctionCall and injected `RunContext`/`fc` | Session ID, native `fc.call_id`, `session_state.praesidia_tools` |
| LangGraph | `langgraph==1.2.11`, `langgraph-checkpoint-sqlite==3.1.1` | `StructuredTool` in `ToolNode`, injected call ID/state, returned `Command` | Configurable thread ID, native tool-call ID, graph state cursor |
| Nous Hermes Agent | upstream commit `0390ace8179f4cf75bd3941e590dd74e638672b6` | Installed module entry point, PluginManager, pre-tool hook, execution middleware, actual tool dispatcher | Native session/tool-call ID, private middleware context, atomic native PluginState |

These are tested Python profiles, not claims about other languages or all future
versions. NVIDIA NemoClaw is a deployment/sandbox profile; it is not a Python
runtime adapter or an additional independent user population. The Hermes package
is separately installable from [plugins/hermes](../plugins/hermes/README.md).

## Application integration

```python
from praesidia import Praesidia
from praesidia.integrations import ManagedProtectedTool, FileRuntimeAttemptStore
from praesidia.integrations.google_adk import google_adk_tool

client = Praesidia(api_key=host_secret, org_id=host_organization)
managed = ManagedProtectedTool(
    client.protected_http,
    attempt_store=FileRuntimeAttemptStore("/private/host-owned/attempts"),
    target_id="operator-registered-record-target",
    name="write_record",
    description="Write the exact independently reviewed record",
)
tool = google_adk_tool(managed)
# Supply tool to the real ADK agent's tools list. Its only model argument is body.
# Persist ADK session state and resume the original native function-call identity.
```

Equivalent lazy factories are `crewai_tool`, `openai_tool`, `microsoft_tool`,
`agno_tool`, and `langgraph_tool` in their corresponding integration modules.
CrewAI requires `binding=lambda: RuntimeBinding(RuntimeCall("crewai", flow_id,
logical_step_id), flow_state["praesidia_tools"], persist=save_flow)`: BaseTool does
not inject a stable native call ID. Do not invent IDs from the body or accept them
as model input. OpenAI's default host context contains `praesidia_thread_id` and
`praesidia_tools`; its optional binding callback can synchronously persist state.

Generic hosts can use `ManagedProtectedTool.invoke(body, RuntimeBinding(...))`.
`attempt_store` is mandatory on `ManagedProtectedTool`. Its atomic durable claim
finishes before resume; framework state, native approval flags and optional
`RuntimeBinding.persist` callbacks cannot replace it. The file implementation uses
private create-only POSIX markers and fsyncs the file and directory ancestors.
Markers contain only approval/action IDs and request commitment; preserve them
across restarts and never delete one to retry an ambiguous effect. Unreadable or
conflicting state blocks dispatch. Multi-host deployments must provide a shared
durable atomic `RuntimeAttemptStore`; an in-memory mapping is insufficient.
`RuntimeBinding.persist` remains optional for the separate native recovery cursor.
Its failure also prevents dispatch, but native delta timing cannot reset a claim.

Every invocation repeats idempotent `prepare`, including after lost local state,
then reads the authoritative checkpoint. Changing body, target, native call or
persisted approval identity cannot borrow an old approval. The backend rechecks
live permission/consent, expiry, review and exact request before execution.
The SDK additionally rejects malformed status/closure/grade, changed readback
identity, expired approval and an approved response without a reviewer.

`approval_required` means persist the real framework state, obtain an independent
Praesidia decision and re-enter **the same logical call**. OpenAI's native local
approval is a wake-up only: approving its serialized RunState does not approve
Praesidia. ADK/Agno/Agent Framework approval UIs are not silently connected here.
LangGraph's existing `integrations.langgraph` durable interrupt graph remains
available; the new ToolNode adapter returns pending state for host coordination.

`recorded_outcome` never means success by itself. Inspect `closure`: `DENIED`,
`FAILED_NO_EFFECT`, `PARTIAL`, `OUTCOME_UNKNOWN`, `EVIDENCE_INCOMPLETE` and other
server closures remain distinct. A consumed action without a closure is
`inspection_required`. After a lost resume response the SDK marks the attempted
cursor and raises `ProtectedToolOutcomeUnknown`; subsequent calls, including fresh processes with lost framework state, read only
and must not blindly dispatch again. Tool cancellation cannot promise that an already
sent HTTP effect was cancelled. Revoke an unconsumed checkpoint through the
existing API and inspect any in-flight action.

Returned receipt/grade are server claims. Independently pin the target and use
`verify_protected_http_result`; `acknowledge` and offline evidence verification
remain explicit operations, not inferred from a framework's successful return.
Hosted OpenAI tools, unrelated local tools, MCP/A2A passthrough and provider/model
calls are outside these managed factories. Existing Praesidia MCP/A2A transport
support does not by itself intercept those separate execution paths.

## Restartable full-backend fixture

The host CLI invokes actual framework classes with a host-issued stable call ID;
it does not require a model or provider key. Use only a private acceptance account
and registered synthetic target. Provide `PRAESIDIA_API_KEY`,
`PRAESIDIA_ORG_ID`, `PRAESIDIA_API_URL` as environment values. The user-backed
credential needs `agents:invoke`, `workflows.execute`, and the corresponding
enabled proof/approval features. A different authorized user approves the request.

```bash
python examples/protected_framework_tool.py \
  --runtime google-adk --state-file /private/owned/adk-state.json \
  --thread-id acceptance-session --call-id native-call-1 \
  --target-id synthetic-write --body-file /private/owned/request.json \
  --expect approval_required
# Independently approve returned approvalId through the actual API/UI.
# Repeat exactly, changing only --expect to recorded_outcome.
```

Supported `--runtime`: `crewai`, `openai-agents`, `google-adk`,
`microsoft-agent-framework`, `agno`, `langgraph`, `hermes`.
For Hermes install its local plugin package and set `PRAESIDIA_HERMES_SOURCE` to
the pinned upstream checkout; the CLI uses an owned sibling `hermes-profile`
whose native plugin-state files and sibling `attempts` directory must survive restart. Keep one private directory
per fixture. CrewAI's fixture-only auth storage is redirected to this directory;
no operator login is loaded. State files contain cursors, not credentials; their
parent directory and body file still require ordinary host access protection.
The CLI requires an absolute state-file path and uses `<state-file>.attempts`
for durable claims. Hermes derives its claim directory from the pinned public
`ctx.state.path.parent / "attempts"` API; plugin cursor writes are not claim CAS.

`tests/test_framework_tools.py` executes real tools over a loopback API fixture,
including separate processes and native OpenAI Runner/RunState approval. This
proves client/framework behavior, not backend authorization or signed evidence.
The same CLI is suitable for the separate whole-stack approval/effect/receipt
acceptance. `tests/test_hermes_plugin.py` uses the actual pinned Hermes package
loader, dispatcher, hooks, middleware and native disk state, not fake classes.

## Sources and stability

Official source/API references accessed 2026-09-06; exact installed package code
is authoritative for the tested profiles where rolling docs differ:

- [CrewAI custom tools](https://docs.crewai.com/en/learn/create-custom-tools), [Flow persistence](https://docs.crewai.com/en/concepts/flows).
- [OpenAI function tools](https://openai.github.io/openai-agents-python/tools/), [human in the loop](https://openai.github.io/openai-agents-python/human_in_the_loop/).
- [Google ADK function tools](https://google.github.io/adk-docs/tools-custom/function-tools/), [state](https://google.github.io/adk-docs/sessions/state/).
- [Microsoft Agent Framework Python](https://github.com/microsoft/agent-framework/tree/main/python).
- [Agno tools](https://docs.agno.com/tools/overview), [runtime context](https://docs.agno.com/agents/run-context).
- [LangGraph interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts), [tool integration](https://docs.langchain.com/oss/python/langchain/tools).
- [Hermes plugin API](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/).


## Required verification profiles

CI requires both base management compatibility on Python3.9/3.11/3.14 and the
locked Python3.11 native-framework job. The **unchanged 90% full-source coverage
floor** now runs in that full dependency profile, with no source exclusions.
The base legs and Python3.14 import-smoke image do not claim coverage for optional
framework packages that are absent or unsupported there. Make both jobs required
in repository branch protection; a base-only success is insufficient.

To reproduce native checks locally in an isolated Python3.11 environment:

```bash
uv sync --frozen --extra dev --extra frameworks
uv pip install --no-deps -e ./plugins/hermes
# Obtain the pinned Hermes checkout documented above, outside this package.
PRAESIDIA_HERMES_SOURCE=/path/to/pinned/hermes \
PRAESIDIA_REQUIRE_FRAMEWORKS=1 PRAESIDIA_REQUIRE_HERMES=1 \
OTEL_SDK_DISABLED=true .venv/bin/coverage run -m pytest tests/ -q
.venv/bin/coverage report
uv pip check
```

This starts only owned loopback API fixtures and isolated runtime profiles. The
telemetry/receipt golden JSON contracts are vendored so the SDK's tests and image
build do not require a sibling checkout. When `shared/` is present, telemetry
fixture parity is additionally checked against its canonical contract.


## Known optional dependency advisory boundary (2026-09-06)

The tested CrewAI1.15.20 package declares `chromadb~=1.1.0`; the environment
resolved ChromaDB1.1.1. `pip-audit==2.9.0 --local --skip-editable` returned four
advisory records: `PYSEC-2026-311`, `GHSA-2wm9-hf6c-p5cr`,
`GHSA-36p7-vc44-83pf`, `GHSA-xph7-9rjv-w5fr`, with no listed fix versions.
The complete optional profile is **not advisory-clean**. No ignore, unsupported
transitive override, or claim that these vulnerabilities are fixed is included.
The base SDK has no CrewAI/Chroma dependency unless an optional extra is selected.

The [reviewed Chroma tenant-authorization advisory](https://github.com/advisories/GHSA-2wm9-hf6c-p5cr)
and [code-injection advisory](https://github.com/advisories/GHSA-36p7-vc44-83pf)
identify Chroma server/API paths; the
[SimpleRBAC advisory](https://github.com/advisories/GHSA-xph7-9rjv-w5fr)
concerns Chroma's server authorization provider. This adapter's tested profile
uses a BaseTool/Flow, never starts a Chroma server or enables Chroma-backed memory,
and dispatches only through Praesidia. That narrows tested execution scope; it
is not a remediation for applications that separately enable affected Chroma
features. Production adoption of the CrewAI extra needs an explicit upstream
resolution or separately reviewed deployment decision. Other native adapters
remain independently installable and testable without selecting CrewAI.
