# Publishing `praesidia` to PyPI

This package has **never been published**. `https://pypi.org/pypi/praesidia/json` returns `404`
(checked 2026-09-26). Version 0.5.0 is the first release. This document is for the person who runs
the publish. It is self-contained, so you should not need to read the rest of the repo first.

A publish is a **tag push**, never a local upload. `.github/workflows/publish.yml` runs on
`push: tags: ['v*']` in two jobs:

1. **build** (read-only token): checks that the tag equals `v` + `pyproject.toml`'s version and
   that the tagged commit is on `origin/main`, runs pytest with the coverage floor,
   `python -m build`, `twine check`, the clean-venv install smoke on that same wheel
   (`scripts/wheel_install_smoke.sh`) and a CycloneDX SBOM, then hands `dist/` to the next job.
2. **publish**: runs in the `pypi` GitHub environment, is the only job with `id-token: write`,
   runs no repository code, and uploads that exact `dist/` with PyPI **trusted publishing**
   (OIDC). It also uploads PEP 740 attestations. There is no PyPI token anywhere: not in the
   repo, not in Actions secrets, not on a laptop.

## Prerequisites (one-time)

1. **PyPI account with 2FA.** PyPI requires 2FA for every account that manages a project.
2. **Check the name is still free.** `praesidia` is a global, unscoped name on PyPI.
   `https://pypi.org/project/praesidia/` must still 404.
3. **Register a pending trusted publisher** on PyPI: *Your account → Publishing → Add a new
   pending publisher → GitHub*, with exactly these values:

   | Field | Value |
   | --- | --- |
   | PyPI project name | `praesidia` |
   | Owner | `praesidia-ai` |
   | Repository name | `sdk-python` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

   A pending publisher lets the first upload create the project, so you do not need a token or a
   manual first upload. It does **not** reserve the name: if someone else registers `praesidia`
   before your first publish, the pending publisher is void.
4. **Create the `pypi` environment** on `praesidia-ai/sdk-python` (*Settings → Environments →
   New environment*). Add yourself as a **required reviewer**, so a pushed tag waits for your
   approval before anything uploads. Under *Deployment branches and tags*, allow only tags
   matching `v*`.
5. **Public source repo.** Attestations link the release to a public GitHub workflow run.
   `praesidia-ai/sdk-python` was public on 2026-09-26 (the unauthenticated GitHub API returns
   200).

## What ships (verified 2026-09-26, `python -m build` + `twine check`)

The **wheel** (`praesidia-0.5.0-py3-none-any.whl`, what `pip install praesidia` pulls) has 41
entries: `praesidia/**` (modules, `integrations/`, `py.typed`) and `praesidia-0.5.0.dist-info/`
(`METADATA`, `WHEEL`, `RECORD`, `licenses/LICENSE`). No tests, `.env`, CI config, Dockerfile,
examples or `plugins/`. Metadata-Version 2.4 (pinned via `hatchling==1.27.0`; Twine 6.2 rejects
2.5). `twine check` passes on both artifacts.

Published extras: `openai-agents`, `google-adk`, `microsoft-agent-framework`, `agno`,
`langgraph`, `frameworks` (all five) and `dev`. **CrewAI is not an extra** while its ChromaDB
advisories are open (`docs/runtime-integrations.md`, "Known optional dependency advisory
boundary"). CI still tests the adapter through the unpublished `crewai` dependency group. To
publish it later, move it back to `[project.optional-dependencies]` after the advisories are
resolved or a reviewed decision accepts them.

The **sdist** (`praesidia-0.5.0.tar.gz`) also carries `tests/`, `docs/`, `examples/` (except
`examples/refund_authorization`), `plugins/hermes/`, `.github/`, `Dockerfile`, `uv.lock` and
`scripts/`. It excludes Git metadata. Once uploaded, a version's files can never be replaced, so
before tagging, check:

```bash
python -m build && python -m twine check dist/*
unzip -Z1 dist/*.whl | grep -v '^praesidia/'                    # only praesidia-0.5.0.dist-info/*
tar tzf dist/*.tar.gz | grep -E '\.venv|/Users|\.env$|/\.git/'  # must print nothing
```

## Steps

```bash
# 1. From a clean, up-to-date main whose pyproject.toml version is the one to release.
git checkout main && git pull
grep '^version' pyproject.toml         # version = "0.5.0" for the first release

# 2. Main must be on GitHub first: the workflow rejects a tag whose commit is not on origin/main.
git push origin main

# 3. Tag that commit and push the tag. This starts the release.
git tag -a v0.5.0 -m "praesidia 0.5.0"
git push origin v0.5.0

# 4. Watch https://github.com/praesidia-ai/sdk-python/actions/workflows/publish.yml
#    When the build job is green, approve the `pypi` environment deployment. A build that fails
#    any check never reaches the publish job.
```

For later releases, bump `[project].version` in `pyproject.toml`, `praesidia/__init__.py`'s
`__version__`, `examples/refund_authorization/requirements.txt` and `uv.lock` (`uv lock`) in one
commit. `tests/test_release_workflow.py` fails if they disagree. A patch release leaves
`plugins/hermes` alone (it accepts `praesidia>=0.5.0,<0.6`); a minor bump fails that test until the
plugin's range, version and `CHANGELOG.md` move too. Add a `CHANGELOG.md` entry, then repeat
steps 1-4 with the new tag.

## After publishing: check it landed

```bash
curl -s https://pypi.org/pypi/praesidia/json | head -c 200   # real JSON, not 404
pip index versions praesidia
python -m venv /tmp/p && /tmp/p/bin/pip install praesidia==0.5.0 \
  && /tmp/p/bin/python -c "from praesidia import Praesidia; print('OK')"
```

On `https://pypi.org/project/praesidia/`, check that the README renders, the license shows MIT,
the classifiers are there, the project links work, and the files show attestations
(*Provenance*). The `Documentation` link points at the GitHub README: `docs.praesidia.ai` does not
resolve yet, and per-release metadata cannot be changed later. Switch it back in the first release
after `docs.praesidia.ai/sdk/python` is live.

Then update every install instruction that says "after publication" (`README.md` "Installation",
`docs/runtime-integrations.md`, `examples/refund_authorization/README.md`), and the website's
install pages.

## If the first publish is wrong

PyPI never lets a filename be reused, even after deletion.

- **Yank** (PyPI web UI: *Manage project → the version → Options → Yank*): the version stays in
  the history, but unpinned installs skip it. `pip install praesidia==<that version>` still works,
  with a warning. Use this for "this version is broken, do not let new installs pick it up".
- **Delete** (PyPI UI "Delete release"): only for an accidental or secret-leaking publish. The
  version number is burned either way. The fix must be a new version number.
- **Wrong metadata only** (description, classifiers, URLs): it is baked into the release's
  `METADATA`, so it needs a new version.

## Semver policy

Same as `sdk`'s (kept in lockstep on purpose; see that repo's `PUBLISHING.md`): standard SemVer.
Before 1.0, a breaking change bumps the **minor** version, and a patch is for backward-compatible
fixes only. `1.0.0` is a deliberate decision, not automatic. Every hand-written API call in
`praesidia/*.py` is checked against a fresh `ui/swagger.json` export by the contract-drift gate
(`.github/workflows/contract-drift.yml`, run locally with
`sdk/scripts/audit-api-contract.mjs --lang py`) before any release.

## Plugins (`plugins/hermes`): not published by this workflow

`plugins/hermes` is a separate package (`praesidia-hermes` 0.1.1, its own `pyproject.toml`,
version and `plugins/hermes/CHANGELOG.md`). `https://pypi.org/pypi/praesidia-hermes/json` returns
`404` (checked 2026-09-26). It depends on `praesidia>=0.5.0,<0.6`, so publish `praesidia` 0.5.0
first. `ci.yml`'s `frameworks` job tests it against the pinned Hermes checkout.

Its metadata and artifacts are release-ready (`cd plugins/hermes && python -m build && python -m
twine check dist/*` passes; the wheel holds only `praesidia_hermes/__init__.py` plus
`dist-info`). It has **no publish workflow**: pushing a `v*` tag on this repo does not publish
it. Publishing it needs a tag-triggered job like `publish.yml`'s, building from `plugins/hermes`,
and a second pending trusted publisher on PyPI for project `praesidia-hermes`.
