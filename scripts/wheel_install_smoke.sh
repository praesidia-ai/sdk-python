#!/usr/bin/env bash
# Install the built wheel the way a user would: fresh venv outside the repo, cwd outside the
# repo, then import the public surface, run the refund example's offline selfcheck, pip check.
# Needs network (isolated build backend + httpx from the index). PYTHON overrides the interpreter.
# WHEEL=<path> smokes that prebuilt wheel instead of building one (publish.yml: the artifact it uploads).
set -euo pipefail
repo="$(cd "$(dirname "$0")/.." && pwd)"
py="${PYTHON:-python3}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

if [ -n "${WHEEL:-}" ]; then
  [ -f "$WHEEL" ] || { echo "WHEEL must name exactly one wheel file: $WHEEL" >&2; exit 1; }
  wheel="$(cd "$(dirname "$WHEEL")" && pwd)/$(basename "$WHEEL")"
else
  # Build into a temp dir so a stale dist/*.whl can never be the one tested.
  "$py" -m build --wheel --outdir "$tmp/dist" "$repo"
  wheel="$(ls "$tmp"/dist/*.whl)"
fi
"$py" -m venv "$tmp/v"
cd "$tmp"
"$tmp/v/bin/python" -m pip install --quiet "$wheel"

want="$(sed -n 's/^version = "\(.*\)"$/\1/p' "$repo/pyproject.toml" | head -1)"
"$tmp/v/bin/python" - "$want" <<'PY'
import sys
import praesidia
from praesidia import Praesidia, PraesidiaInteractionHooks, AsyncPraesidiaInteractionHooks, jcs_commitment
assert "site-packages" in praesidia.__file__, praesidia.__file__
assert praesidia.__version__ == sys.argv[1], (praesidia.__version__, sys.argv[1])
print(f"wheel import OK: praesidia {praesidia.__version__} from {praesidia.__file__}")
PY

cp -R "$repo/examples/refund_authorization" "$tmp/example"
(cd "$tmp/example" && "$tmp/v/bin/python" selfcheck.py)
"$tmp/v/bin/python" -m pip check
echo "wheel install smoke OK"
