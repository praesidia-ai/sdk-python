"""The repo must not track symlinks, and a symlinked .venv must stay ignored (SDK-0356)."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None
    or subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--git-dir"], capture_output=True).returncode != 0,
    reason="no git, or not a git checkout (e.g. an sdist or the image build)",
)


def test_no_tracked_symlinks() -> None:
    staged = _git("ls-files", "-s").stdout.splitlines()
    assert [line for line in staged if line.startswith("120000 ")] == []


def test_venv_symlink_is_ignored(tmp_path: Path) -> None:
    # A lane links .venv to the source checkout; `.venv/` only matches directories.
    # Check the pattern against a symlink in a scratch repo carrying this .gitignore.
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text((ROOT / ".gitignore").read_text())
    (tmp_path / ".venv").symlink_to(tmp_path / "elsewhere")
    result = subprocess.run(
        ["git", "-C", str(tmp_path), "check-ignore", "-v", ".venv"], capture_output=True, text=True
    )
    assert result.returncode == 0, "a .venv symlink is not ignored by .gitignore"
