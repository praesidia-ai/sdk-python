# `praesidia` (Python SDK) — operations

Condensed build/test/publish reference. `PUBLISHING.md` at repo root is the authoritative
publish runbook — read that in full before ever pushing a release tag.

## Requirements

Python `>=3.9` (`pyproject.toml:14`). `httpx` is the only required runtime dependency
(`sdk-python/README.md:20`). Packaged with `hatch` (`pyproject.toml:62-68`).

## Local development

```bash
cd core/sdk-python
uv sync --extra dev        # this repo's committed uv.lock is the source of truth
uv run pytest -q
```

Without `uv`, a plain venv works too (`sdk-python/README.md:568-575`):

```bash
pip install -e ".[dev]"
pytest
```

## Contract-drift gate (CD-0007)

```bash
node ../sdk/scripts/audit-api-contract.mjs <path-to-swagger.json> --source praesidia --lang py
```

Requires a sibling checkout of `sdk` (owns the scanner). Wired as its own CI job
(`.github/workflows/contract-drift.yml`) with sibling checkouts of `sdk`, `be-core`, and
`queue-core` (`sdk-python/README.md:591-596`).

## Building an unreleased feature locally (no publish)

Since the package has never been published (`PUBLISHING.md:3`):

```bash
python -m build            # produces dist/*.whl and dist/*.tar.gz
pip install dist/praesidia-<version>-py3-none-any.whl
```

A successful local build does not publish a PyPI release (`sdk-python/README.md:18-19`).

## Publishing (summary — see `PUBLISHING.md` for the actual runbook)

Publishing is a **tag push**, not a local `twine upload`; `.github/workflows/publish.yml` on
`push: tags: ['v*']` is the only place `PYPI_API_TOKEN` is used. As of this writing the package
has never been published — `https://pypi.org/pypi/praesidia/json` returns `404`
(`PUBLISHING.md:3`). PyPI's project-name registration is global/unscoped (unlike npm's `@praesidia`
scope) — `PUBLISHING.md`'s prerequisite #1 flags confirming `praesidia` is still unclaimed before
relying on this flow.

## Failure modes — what to check first

| Symptom | Likely cause | Where to look |
|---|---|---|
| `pip install praesidia` fails / 404 | Package genuinely unpublished | `PUBLISHING.md:3`; build a local wheel instead |
| A documented method is missing at runtime | Consumer has an older/lagging registry release (once one exists) vs. this checkout | `sdk-python/README.md:14-18` |
| Contract-drift job fails | `be`'s OpenAPI spec moved without a matching SDK update | re-run `audit-api-contract.mjs --lang py` against a fresh spec |
| `docs.praesidia.ai/sdk/python` link (in `pyproject.toml`) doesn't resolve | Known — the hostname has no DNS record as of 2026-09-12 | `docs/README.md`'s "Publishing status" section; report to `sdk-dev`, don't silently point elsewhere |
| Integration adapter import fails | `praesidia/integrations/*` are optional; check `pyproject.toml`'s `[project.optional-dependencies]` for the extra that ships the adapter's own dependency | `pyproject.toml:33-` |

## Verification limits

Commands verified against `pyproject.toml`, `PUBLISHING.md`, and `README.md` this pass
(2026-09-12); the DNS claim was checked live (`nslookup docs.praesidia.ai`) in this same pass.
Not independently re-run: a full `uv run pytest`/`python -m build` in this session.
