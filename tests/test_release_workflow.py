"""Regression contract for the public package release workflow."""

from pathlib import Path


def test_release_tag_commit_must_be_contained_in_main():
    workflow = (
        Path(__file__).resolve().parents[1] / ".github" / "workflows" / "publish.yml"
    ).read_text(encoding="utf-8")

    assert "fetch-depth: 0" in workflow
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" origin/main' in workflow
