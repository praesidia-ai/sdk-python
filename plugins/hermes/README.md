# Praesidia for Nous Hermes Agent

This separately installable package registers `praesidia_protected_http` and a
default strict tool boundary. It targets **Nous Hermes Agent**, not the unrelated
formerly named HermesOS hosting service. Tested against upstream commit
`0390ace8179f4cf75bd3941e590dd74e638672b6` (2026-09-06).

Install the locally built `praesidia` 0.4.1 wheel, then `pip install ./plugins/hermes`
in the Hermes Python environment. Nothing is published by this repository.
Enable the `praesidia` entry-point plugin in the intended Hermes profile and set:

```yaml
plugins:
  entries:
    praesidia:
      enabled: true
      settings:
        target_id: your-backend-registered-target
        strict_tools: true
```

Set `PRAESIDIA_API_KEY`, `PRAESIDIA_ORG_ID`, and `PRAESIDIA_API_URL` in the host's
secret environment, outside model messages and plugin state. The user-backed
credential must have `agents:invoke`, the required user permission, and enabled
protected-action/approval features. Independently approve the exact request in
Praesidia; re-enter the original native session/tool-call ID. A newly generated
model call ID intentionally creates a new approval, never resumes the old one.

Verify Hermes' plugin listing shows this plugin enabled, its tool, pre-tool hook
and execution middleware. The managed handler refuses calls when its middleware
is skipped or native IDs are absent. Hooks alone are insufficient: this tested
Hermes version can skip ordinary callback failures. Strict mode returns an
explicit synchronous block for unrelated dispatched tools, and the execution
middleware repeats that boundary. `strict_tools: false` explicitly narrows this
to one managed tool; unrelated tools retain their normal behavior.

Hermes' native PluginState stores approval and attempted-dispatch cursors using
locked atomic writes before dispatch. A lost response is inspected through the
backend; it is not blindly retried. The backend dispatches the configured target;
this package accepts no local effect callable, arbitrary URL or approval Boolean.

This profile is not an OS sandbox or universal interception of model providers,
code running outside the tool dispatcher, other plugins, hosted remote effects,
or operator-disabled hooks. Pair it with an independently configured sandbox or
network boundary where that broader enforcement is required. Return values and
evidence grades are backend declarations until independently verified.

Primary APIs: [plugin contract](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/),
[pinned execution middleware](https://github.com/NousResearch/hermes-agent/blob/0390ace8179f4cf75bd3941e590dd74e638672b6/hermes_cli/middleware.py),
[pinned dispatcher](https://github.com/NousResearch/hermes-agent/blob/0390ace8179f4cf75bd3941e590dd74e638672b6/model_tools.py).

The plugin requires the pinned public `ctx.state.path` API and stores durable
dispatch claims in its profile-owned sibling `attempts` directory. Preserve this
directory across reloads/restarts. Its private create-only markers contain only
approval/action IDs and the request commitment, and fsync completes before resume.
Native PluginState remains a recovery cursor; it cannot reset the independent
claim. Unreadable/conflicting claims fail closed. Never delete markers to retry
an uncertain effect; shared multi-host profiles require an atomic shared store.
