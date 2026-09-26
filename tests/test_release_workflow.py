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
# one build, smoke on that dist/ wheel, then hand dist/ over with no rebuild in between.
# INTEG-0110 — trusted publishing: only the publish job holds `id-token: write`, it runs
# no repo code, and no stored token exists anywhere in the workflow.
def test_publish_uploads_the_smoked_wheel_via_trusted_publishing():
    import re

    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
    build_job, publish_job = workflow.split("\n  publish:\n", 1)
    build = build_job.index("python -m build")
    smoke = build_job.index("bash scripts/wheel_install_smoke.sh")
    handoff = build_job.index("name: dist\n")
    assert build < smoke < handoff
    assert workflow.count("-m build") == 1
    smoke_step = build_job[build_job.rindex("- name:", 0, smoke) : handoff]
    assert 'WHEEL="$(ls dist/*.whl)" bash scripts/wheel_install_smoke.sh' in smoke_step

    assert "secrets." not in workflow
    assert "twine upload" not in workflow
    assert "id-token" not in build_job
    assert "needs: build" in publish_job
    assert "id-token: write" in publish_job
    assert "name: pypi\n" in publish_job
    assert "actions/checkout" not in publish_job
    assert "run:" not in publish_job
    assert "name: dist\n" in publish_job
    assert re.search(r"uses: pypa/gh-action-pypi-publish@[0-9a-f]{40} ", publish_job)

    script = (root / "scripts" / "wheel_install_smoke.sh").read_text(encoding="utf-8")
    assert '"${WHEEL:-}"' in script


# INTEG-0110 — CrewAI 1.15.20 resolves ChromaDB 1.1.1 with open advisories. Until that
# is resolved the published metadata must not offer it as an extra; CI still tests the
# adapter through an unpublished dependency group.
def test_crewai_is_not_a_published_extra_while_its_advisories_are_open():
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")

    def section(name):
        body = pyproject.split(f"\n[{name}]\n", 1)[1].split("\n[", 1)[0]
        return "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))

    assert "crewai" not in section("project.optional-dependencies")
    assert "crewai = [" in section("dependency-groups")
    ci = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "--extra frameworks --group crewai" in ci


def test_first_public_release_is_0_5_0_everywhere_the_version_is_asserted():
    import re

    import praesidia

    root = Path(__file__).resolve().parents[1]

    def read(p):
        return (root / p).read_text(encoding="utf-8")

    version = re.search(r'^version = "(.*)"$', read("pyproject.toml"), re.M).group(1)
    assert version == "0.5.0"
    assert praesidia.__version__ == version
    assert f'name = "praesidia"\nversion = "{version}"' in read("uv.lock")
    reqs = read("examples/refund_authorization/requirements.txt").split()
    assert reqs == [f"praesidia>={version}"]


def test_hermes_plugin_admits_sdk_patches_but_not_the_next_breaking_minor():
    import re

    from packaging.requirements import Requirement
    from packaging.version import Version

    import praesidia

    plugin = Path(__file__).resolve().parents[1] / "plugins" / "hermes"
    meta = (plugin / "pyproject.toml").read_text(encoding="utf-8")
    deps = re.search(r"^dependencies = \[(.*)\]$", meta, re.M).group(1)
    (req,) = [Requirement(d) for d in re.findall(r'"([^"]+)"', deps)]
    sdk = Version(praesidia.__version__)

    assert req.name == "praesidia"
    assert sdk in req.specifier
    assert Version(f"{sdk.major}.{sdk.minor}.{sdk.micro + 1}") in req.specifier
    # Pre-1.0 a minor bump is breaking, and the plugin imports praesidia.integrations internals.
    assert Version(f"{sdk.major}.{sdk.minor + 1}.0") not in req.specifier

    version = re.search(r'^version = "(.*)"$', meta, re.M).group(1)
    assert f"## {version} " in (plugin / "CHANGELOG.md").read_text(encoding="utf-8")
