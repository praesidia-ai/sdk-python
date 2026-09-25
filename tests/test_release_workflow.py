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


# SDK-0344 — the tag uploads exactly the wheel the install smoke (SDK-0331) accepted:
# one build, smoke on that dist/ wheel, then upload dist/* with no rebuild in between.
def test_publish_smokes_the_uploaded_wheel_before_upload():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    upload = workflow.index("twine upload --non-interactive dist/*")
    build = workflow.index("python -m build")
    smoke = workflow.index("bash scripts/wheel_install_smoke.sh")
    assert build < smoke < upload
    assert workflow.count("-m build") == 1
    smoke_step = workflow[workflow.rindex("- name:", 0, smoke) : upload]
    assert 'WHEEL="$(ls dist/*.whl)" bash scripts/wheel_install_smoke.sh' in smoke_step
    assert "secrets.PYPI_API_TOKEN" in workflow

    script = (root / "scripts" / "wheel_install_smoke.sh").read_text(encoding="utf-8")
    assert '"${WHEEL:-}"' in script


def test_first_public_release_is_0_5_0_everywhere_the_version_is_asserted():
    import re

    import praesidia

    root = Path(__file__).resolve().parents[1]

    def read(p):
        return (root / p).read_text(encoding="utf-8")

    version = re.search(r'^version = "(.*)"$', read("pyproject.toml"), re.M).group(1)
    assert version == "0.5.0"
    assert praesidia.__version__ == version
    assert f'dependencies = ["praesidia=={version}"]' in read("plugins/hermes/pyproject.toml")
    assert f'name = "praesidia"\nversion = "{version}"' in read("uv.lock")
    reqs = read("examples/refund_authorization/requirements.txt").split()
    assert reqs == [f"praesidia>={version}"]
