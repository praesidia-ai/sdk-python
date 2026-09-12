# `praesidia` (Python SDK) — docs

Dated 2026-09-12. Owner (per `core/.claude/POLICY.md:101`): `sdk-dev`. Written for
`DOCS-0001-sdk-python`.

This `docs/` directory holds two existing topic files (`federated-identity.md`,
`runtime-integrations.md`) plus the DOCS-0001 triad added here. The root `sdk-python/README.md`
is the primary reference; read that first. This triad adds a platform-fit statement, a module map
with `path:line` anchors, and a condensed build/test/publish reference.

## What it is

`praesidia` is a Python management SDK for the Praesidia AI agent platform: agents, agent-tasks,
workflows, connections, audit log, analytics, EU AI Act compliance reports, agent memory, OTLP
GenAI telemetry, and offline trust-passport verification, all via the Praesidia REST API
(`sdk-python/README.md:1-7`). Requires Python 3.9+ and `httpx`
(`sdk-python/README.md:20`).

## Publishing status — read this before writing install instructions anywhere

**`praesidia` has never been published to PyPI.** `sdk-python/PUBLISHING.md:3` states
`https://pypi.org/pypi/praesidia/json` returned `404` as of 2026-09-11. `PUBLISHING.md` describes
the **intended** flow (tag push → `.github/workflows/publish.yml` → `twine upload` via
`PYPI_API_TOKEN`) — a documented plan, not a completed action. `sdk-python/README.md:14-18`
already carries the correct disclaimer: it "describes the current source checkout", a registry
release may lag, and unreleased features require a local `python -m build` + wheel install; a
successful local build does not publish a PyPI release.

**Found and not fixed here**: `pyproject.toml:58` sets `Documentation =
"https://docs.praesidia.ai/sdk/python"`. That hostname does not currently resolve — `nslookup
docs.praesidia.ai` returns "No answer"/NXDOMAIN (checked live 2026-09-12, matches this run's
broader finding that several public promises are 404/NXDOMAIN). This is a metadata claim in
`pyproject.toml`, which is package config, not a docs file — reporting it rather than editing it;
`sdk-dev` should either stand up that URL or point `Documentation` at something that resolves
(e.g. this `docs/` directory's GitHub path) before the next PyPI release.

## Where it sits in the platform

- **Upstream dependency**: `be`'s REST API, via `praesidia/client.py`'s HTTP client
  (`_http.py`). Each `praesidia/*.py` module hand-writes `be`'s REST routes/body shapes.
- **Parity partner**: `sdk` (TypeScript) — several features are built to match it (pagination,
  structured error envelope, retry policy — see the version history in
  `sdk-python/README.md`'s Changelog section, each entry cross-referencing the same `FINDING-*`/
  `SCAN2-*` ticket IDs as the TypeScript SDK).
- **Plugins**: `plugins/hermes` — a separate local package adding a Nous Hermes Agent managed
  tool (`sdk-python/README.md:26-27`).
- **Contract verification**: `CD-0007` — `praesidia/*.py`'s hand-written routes/shapes are
  checked against `be` for drift (`sdk-python/README.md:578-591`, see `ARCHITECTURE.md`).

## Verification limits

Source-verified this pass: module list via `praesidia/` directory listing and `__init__.py`
exports; publish status via `PUBLISHING.md`; the `docs.praesidia.ai` DNS claim was checked live
(`nslookup`, this pass, 2026-09-12) rather than repeated from `pyproject.toml`, per this ticket's
own instruction. Public-facing note: this package is open-source — this triad states capabilities,
not internal mechanism detail, consistent with the parent ticket's instruction.

See also: `ARCHITECTURE.md`, `OPERATIONS.md`, `PUBLISHING.md` (authoritative publish runbook),
and the two existing topic docs.
