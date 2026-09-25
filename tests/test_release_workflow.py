"""Regression contract for the public package release workflow."""

from pathlib import Path


def test_release_tag_commit_must_be_contained_in_main():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "publish.yml"
    ).read_text(encoding="utf-8")

    assert "fetch-depth: 0" in workflow
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" origin/main' in workflow


def test_ci_smokes_the_built_wheel_in_a_clean_venv_on_oldest_and_newest_python():
    root = Path(__file__).resolve().parents[1]
    ci = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    matrix = ci.split("python-version: [", 1)[1].split("]", 1)[0]
    oldest, newest = matrix.split(",")[0].strip(), matrix.split(",")[-1].strip()
    step = ci.split("- name: Smoke the built wheel in a clean venv", 1)[1].split("- name:", 1)[0]
    assert f"matrix.python-version == {oldest}" in step
    assert f"matrix.python-version == {newest}" in step
    assert "bash scripts/wheel_install_smoke.sh" in step

    script = (root / "scripts" / "wheel_install_smoke.sh").read_text(encoding="utf-8")
    for needle in ("-m build", "-m venv", ".whl", "__version__", "selfcheck.py", "pip check"):
        assert needle in script
