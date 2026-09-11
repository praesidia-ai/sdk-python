# Publishing `praesidia` to PyPI

This package has **never been published**. `https://pypi.org/pypi/praesidia/json` returns `404`
as of 2026-09-11. This document is for the human who runs the publish — it is intentionally
self-contained; you should not need to read the rest of the repo first.

Publishing is a **tag push**, not a local `twine upload`. `.github/workflows/publish.yml` runs on
`push: tags: ['v*']`, re-runs the full test suite + build, and is the only place
`PYPI_API_TOKEN` is used. No local machine ever holds the publish credential.

## Prerequisites (one-time)

1. **Register the project name on PyPI first.** Unlike npm scoped packages, `praesidia` on PyPI
   is a global, unscoped name — confirm it is still unclaimed
   (`https://pypi.org/project/praesidia/` should 404) before relying on this flow; a same-named
   package landing there between now and your first publish would block you.
2. A **PyPI API token** scoped to the `praesidia` project (create the project first via a manual
   `twine upload` of the very first version if PyPI requires the project to exist before a
   project-scoped token can be minted — otherwise use an account-scoped token for the first
   publish only, then narrow it to project-scoped and rotate).
3. `PYPI_API_TOKEN` repository secret set on `praesidia-ai/sdk-python` (GitHub → repo → Settings →
   Secrets and variables → Actions).
4. PyPI 2FA is mandatory account-wide for all publishers as of PyPI's 2023+ policy — this is
   satisfied by using an **API token** (not username/password) for the upload, which is exactly
   what `publish.yml` does (`TWINE_USERNAME: __token__`). No interactive 2FA prompt happens in CI.
5. Trusted Publishing (OIDC, no stored token) is a stronger alternative PyPI now supports for
   GitHub Actions — not configured here; using a stored `PYPI_API_TOKEN` secret is fine but is a
   candidate follow-up hardening once the project exists on PyPI (chicken-and-egg: OIDC trusted
   publishing can only be configured for a project that already exists).

## What ships (verified 2026-09-11, `python -m build` + `twine check`)

The **wheel** (`praesidia-0.4.1-py3-none-any.whl`, what `pip install praesidia` actually pulls)
contains only `praesidia/**` (23 modules + `integrations/` subpackage + `py.typed` marker) and
`dist-info` metadata/license — **no tests, no `.env`, no CI config, no Dockerfile, no plugins/**.
`twine check` passes on both artifacts.

The **sdist** (`praesidia-0.4.1.tar.gz`, a fallback source archive, rarely what a consumer
actually installs since the wheel is pure-Python/universal) additionally includes `tests/`,
`.github/`, `Dockerfile`, `uv.lock`, and `plugins/hermes/` — this is normal Python-ecosystem
practice (hatchling includes all git-tracked files by default; there is no `MANIFEST.in`
restricting it) and contains nothing sensitive, but if you want a leaner sdist add exclude
patterns to `[tool.hatch.build.targets.sdist]` in `pyproject.toml` before the first tag — once
published, that version's sdist contents cannot be changed.

## Steps

```bash
# 1. From a clean main, decide the version (see semver policy below; first release keeps the
#    manifest's current version — see README/PUBLISHING rationale in this repo's ticket report).
git checkout main && git pull

# 2. Bump pyproject.toml's [project].version by hand (no `npm version`-equivalent tool wired here)
#    and commit.
#    e.g. edit pyproject.toml: version = "0.4.2"
git add pyproject.toml
git commit -m "chore: bump version to 0.4.2"
git push origin main

# 3. Tag the exact commit that has that version and push the tag — this is the action that is
#    otherwise irreversible below.
git tag v0.4.2
git push origin v0.4.2

# 4. Watch the Actions run:
#    https://github.com/praesidia-ai/sdk-python/actions/workflows/publish.yml
#    It re-verifies tag == pyproject.toml version, re-runs pytest with coverage, `python -m
#    build`, `twine check dist/*`, then `twine upload` — an artifact that fails the repo's own
#    gates never reaches PyPI.
```

If cutting the very first release, the manifest's current version has never been tagged — tag the
current commit as-is (no bump needed): `git tag v0.4.1 && git push origin v0.4.1`.

## After publishing — verify it actually landed

```bash
curl -s https://pypi.org/pypi/praesidia/json | head -c 200   # should be real JSON, not 404
pip index versions praesidia                                  # confirm the version list
pip install --dry-run praesidia==<version>                    # or a real venv install + import
python -c "from praesidia import Praesidia; print('OK')"
```

Also check the PyPI project page renders correctly:
`https://pypi.org/project/praesidia/` — README, license (MIT), classifiers, and the
`Documentation` project URL. **Note**: `pyproject.toml`'s `Documentation =
"https://docs.praesidia.ai/sdk/python"` **does not resolve in DNS today** (verified 2026-09-11) —
the PyPI page will show a dead link until that host is live. This is a known, tracked gap
(`MKT-0002`), not something this publish step can fix; flag to whoever owns DNS/docs hosting
before or right after the first publish so the link isn't dead on day one.

## If the first publish is wrong

PyPI has **no unpublish/yank-then-reuse** story for the version number — once uploaded, a
filename can never be reused, even after deletion.

- **`pip yank`** (via the PyPI web UI: Manage project → the version → "Yank release"): the
  version stays visible in history but is excluded from unpinned resolution (`pip install
  praesidia` skips it; `pip install praesidia==<that version>` still works with a warning). This
  is the correct tool for "this version is broken, don't let new installs pick it up by default."
  Prefer this over full deletion.
- **Full delete** (PyPI UI "Remove release"): only for genuinely accidental/secret-leaking
  publishes. The version number is burned forever either way — you cannot re-upload
  `praesidia==0.4.1` after deleting it. The next fix must be a new version number.
- **Wrong metadata only** (description, classifiers, URLs): PyPI project metadata (not
  per-release) can be edited without a new release for description/URLs configured at the project
  level; per-release metadata (what's baked into `PKG-INFO`) requires a new version.

## Semver policy going forward

Same as `sdk`'s (kept in lockstep intentionally, see that repo's `PUBLISHING.md`): standard
SemVer, pre-1.0 (`0.x`) breaking changes land as a **minor** bump, patch is reserved for
backward-compatible fixes. `1.0.0` is a deliberate decision, not automatic. Every hand-written API
call in `praesidia/*.py` is checked against a fresh `be/openapi.json` export by the
contract-drift gate (`.github/workflows/contract-drift.yml`, mirrored locally via
`sdk/scripts/audit-api-contract.mjs --lang py`) before any release, published or not — see this
repo's ticket report for the current parity verdict.

## Plugins (`plugins/hermes`) — out of scope here

`plugins/hermes` is a separate PyPI-shaped package (`praesidia-hermes`, its own
`pyproject.toml`/version) exercised by `ci.yml`'s `frameworks` job but has **no publish workflow
of its own**. Pushing a `v*` tag on this repo's root does not publish it. Publishing
`praesidia-hermes` is a separate, unscoped follow-up.
