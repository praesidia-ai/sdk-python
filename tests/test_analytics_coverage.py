"""AUD-0063 — analytics resource coverage gate (Python SDK).

Mirrors ``sdk/src/analytics.coverage.spec.ts`` (and structurally, mcp's
AUD-0062 gate, ``trust-level-thresholds.test.ts``): derives the real
``/organizations/{orgId}/analytics*`` route list from be-core's exported
OpenAPI spec and fails on any operation ``AnalyticsResource`` does not
implement, so a future be-added analytics route goes RED here instead of
silently missing the Python SDK.

The routes under test come from ``extract_analytics_operations()`` (real
spec paths) -- NOT a second hand-authored route list. ``COVERAGE`` only
supplies, per REAL route, which ``AnalyticsResource`` method is supposed to
satisfy it; a route with no entry (or whose entry names a method that does
not exist) fails with a specific, actionable message.

Close-out review (lead) -- ``be`` is a SEPARATE git repo, never a dependency
of ``sdk-python``: this repo must still build/test standalone for any
checkout without a be sibling (a bare ``sdk-python`` clone, ``ci.yml``, or
this package's own published PyPI sdist/wheel). The live comparison below
therefore SKIPS (never raises) when no spec is found, and runs for real only
when one is -- which two concrete layouts both resolve, mirroring mcp's/
sdk's mechanism so all three solve this cross-repo problem the same way, not
a third variant:

  1. Local monorepo dev checkout -- ``sdk-python`` and ``ui`` as siblings
     under ``core/`` (``../../ui/swagger.json`` from this file).
  2. CI: ``.github/workflows/contract-drift.yml`` checks this repo out to
     ``path: sdk-python`` and freshly exports be-core's OpenAPI spec to
     ``../swagger.generated.json`` relative to its ``be-core/`` checkout --
     i.e. the job's workspace root, ``../../swagger.generated.json`` from
     this file. That job now ALSO runs this test file (see its "AUD-0063"
     step) with the export present, so the live comparison genuinely
     executes -- and can genuinely fail -- on every PR. ``ci.yml``, the
     workflow that runs ``pytest``, checks out ``sdk-python`` alone --
     neither candidate exists there, so this suite SKIPS on that job
     specifically; expected, not a gap, because ``contract-drift.yml`` is
     the job that actually catches drift.

``AUD_0063_REQUIRE_SWAGGER`` (mirrors mcp's ``AUD_0062_REQUIRE_BE_SOURCE``)
-- set to the literal string ``"true"`` ONLY in contract-drift.yml's step
env (a job whose entire reason to exist is having a fresh be export), turns
"can't find a spec" from a skip into a HARD test failure. Without it, a
future checkout-shape change that silently broke both candidates would still
report the anchor test ``skipped`` -- not failed -- and this file's other
(unconditional, fixture-based) tests would still pass, so the job would go
green having never actually compared against be. Everywhere else (local dev
without a spec, ``ci.yml``, this package's own PyPI artifact) the var is
unset, so the original standalone-safe skip is unchanged.

``extract_analytics_operations`` and the per-operation coverage check
(``assert_operation_covered``) are unit tested against fixtures below, so
the extraction/comparison logic itself stays proven even when both live
paths are absent (skipped).
"""

from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
from typing import Any, Sequence

import httpx
import pytest
import respx

from praesidia import Praesidia

BASE_URL = "http://test.local"
ORG_ID = "org-1"
ANALYTICS_PREFIX = "/organizations/{orgId}/analytics"

_HERE = Path(__file__).resolve().parent

#: Relative candidates, tried in order against this file's own directory --
#: exported so the CI-layout resolution can be unit tested against fixture
#: directories (below) rather than trusted on hand-worked relative-path
#: arithmetic alone. Mirrors sdk's ANALYTICS_SPEC_RELATIVE_CANDIDATES and
#: mcp's BE_TRUST_SERVICE_CANDIDATES (AUD-0062) -- same literal strings as
#: the TS SDK's, since both files sit at the same relative depth from their
#: repo roots in both the local-monorepo and CI checkout layouts.
ANALYTICS_SPEC_RELATIVE_CANDIDATES: tuple[str, ...] = (
    "../../ui/swagger.json",  # 1. local monorepo dev checkout (sdk-python + ui siblings under core/)
    "../../swagger.generated.json",  # 2. contract-drift.yml CI export (workspace root)
)


def find_first_existing(base_dir: Path, candidates: Sequence[str]) -> Path | None:
    """
    Pure path-resolution helper, factored out so the CI-layout fix can be
    unit tested against fixture directories rather than trusted on
    hand-worked relative-path arithmetic alone. Mirrors sdk's/mcp's
    ``findFirstExisting``.
    """
    for candidate in candidates:
        resolved = (base_dir / candidate).resolve()
        if resolved.exists():
            return resolved
    return None


def _resolve_spec_path() -> Path | None:
    # BE_SWAGGER_PATH stays available as an explicit override (matches
    # scripts/audit-api-contract.mjs's own convention) without disturbing
    # the two real checkout layouts below. A bad/missing override silently
    # falls through to those -- this module never raises on a missing spec,
    # only skips (or, under AUD_0063_REQUIRE_SWAGGER, fails loudly through
    # the anchor test below -- never a silent pass).
    env_override = os.environ.get("BE_SWAGGER_PATH")
    if env_override:
        candidate = Path(env_override).resolve()
        if candidate.exists():
            return candidate
    return find_first_existing(_HERE, ANALYTICS_SPEC_RELATIVE_CANDIDATES)


SPEC_PATH = _resolve_spec_path()
SPEC_AVAILABLE = SPEC_PATH is not None
REQUIRE_SWAGGER = os.environ.get("AUD_0063_REQUIRE_SWAGGER") == "true"

if not SPEC_AVAILABLE:
    _candidates_str = ", ".join(ANALYTICS_SPEC_RELATIVE_CANDIDATES)
    _msg = (
        "[AUD-0063] no be swagger.json found at any known path (BE_SWAGGER_PATH, "
        f"{_candidates_str} relative to tests/) -- live analytics-route coverage check "
    )
    _msg += (
        "will FAIL (AUD_0063_REQUIRE_SWAGGER=true -- this job must have a real spec)."
        if REQUIRE_SWAGGER
        else "skipped so sdk-python can still build/test standalone. Runs for real in the "
        "local monorepo checkout and in contract-drift.yml's CI job, both of which have a "
        "real spec."
    )
    warnings.warn(_msg, stacklevel=1)


def extract_analytics_operations(spec: dict[str, Any]) -> list[tuple[str, str]]:
    """Pure extraction, factored out so it can be unit tested against a fixture spec dict below."""
    ops: list[tuple[str, str]] = []
    for path, methods in spec.get("paths", {}).items():
        if path != ANALYTICS_PREFIX and not path.startswith(f"{ANALYTICS_PREFIX}/"):
            continue
        suffix = path[len(ANALYTICS_PREFIX) :] or "/"
        for method in methods:
            if method.lower() in ("get", "post"):
                ops.append((method.upper(), suffix))
    return sorted(ops)


OPERATIONS: list[tuple[str, str]] = (
    extract_analytics_operations(json.loads(SPEC_PATH.read_text())) if SPEC_PATH else []
)

#: Real-swagger-route -> AnalyticsResource method mapping: (method_name, args, kwargs).
COVERAGE: dict[str, tuple[str, tuple[Any, ...], dict[str, Any]]] = {
    "GET /": ("usage", (), {}),
    "GET /capture-state": ("capture_state", (), {}),
    "GET /agents/{agentId}": ("agent_analytics", ("agent-1",), {}),
    "GET /events": ("events", (), {}),
    "POST /events": ("record_event", (), {"event_type": "REQUEST"}),
    "GET /activity-log": ("activity_log", (), {}),
    "GET /advanced/agent-performance": ("agent_performance", (), {}),
    "GET /advanced/security": ("security_metrics", (), {}),
    "GET /advanced/cost-trends": ("cost_trends", (), {}),
    "GET /advanced/usage-heatmap": ("usage_heatmap", (), {}),
    "GET /advanced/top-agents": ("top_agents", (), {}),
    "GET /advanced/compliance": ("compliance_metrics", (), {}),
    "GET /advanced/anomalies": ("anomalies", (), {}),
    "GET /advanced/cost-by-team": ("cost_by_team", (), {}),
    "GET /export": ("export", (), {}),
    "GET /advanced/model-comparison": ("model_comparison", (), {}),
}


def _client() -> Praesidia:
    return Praesidia(api_key="sk-test", org_id=ORG_ID, base_url=BASE_URL)


def assert_operation_covered(client: Praesidia, method: str, suffix: str) -> None:
    """
    Per-operation coverage check, factored out of the parametrized test body
    so a fixture test below can assert it fails on a synthetic unmapped
    route WITHOUT needing a live spec -- direct proof the gate is capable of
    failing, independent of ``SPEC_AVAILABLE``.
    """
    key = f"{method} {suffix}"
    entry = COVERAGE.get(key)
    if entry is None:
        pytest.fail(
            f"AUD-0063: be exposes {key} (organizations/{{orgId}}/analytics"
            f"{'' if suffix == '/' else suffix}) with no AnalyticsResource coverage entry "
            "and no allow-list reason. Add a resource method (both SDKs) or allow-list it "
            "in COVERAGE with a stated reason."
        )
        return  # unreachable -- pytest.fail raises -- keeps `entry` narrowed below
    method_name, args, kwargs = entry

    fn = getattr(client.analytics, method_name, None)
    if not callable(fn):
        pytest.fail(
            f"AUD-0063: be exposes {key} but AnalyticsResource has no method "
            f"'{method_name}' yet (the coverage map references it -- add the method to "
            "close the gap)."
        )
        return  # unreachable

    expected_suffix = suffix.replace("{agentId}", "agent-1")
    expected_path = f"/organizations/{ORG_ID}/analytics" + (
        "" if expected_suffix == "/" else expected_suffix
    )
    mock_route = respx.get if method == "GET" else respx.post

    with respx.mock:
        route = mock_route(f"{BASE_URL}{expected_path}").mock(
            return_value=httpx.Response(200, json={})
        )
        fn(*args, **kwargs)
        assert route.called, (
            f"AUD-0063: {method_name}() did not call {method} {expected_path} -- "
            "coverage entry path/method mismatch."
        )


def test_be_swagger_json_is_available_and_exports_the_known_analytics_paths():
    """
    Anchor test -- carries the "this job cannot go green without genuinely
    comparing" guarantee. Skips when the spec is genuinely absent AND this
    run doesn't require it; under AUD_0063_REQUIRE_SWAGGER=true
    (contract-drift.yml) it never skips, so a broken checkout shape fails
    this test explicitly instead of silently reporting "skipped".
    """
    if not SPEC_AVAILABLE:
        if not REQUIRE_SWAGGER:
            pytest.skip("no be swagger.json found at any known path -- see module warning")
        pytest.fail(
            "AUD_0063_REQUIRE_SWAGGER=true but no swagger.json was found at any known path "
            f"({', '.join(ANALYTICS_SPEC_RELATIVE_CANDIDATES)} relative to tests/) -- this "
            "job's only reason to exist is comparing against a real be-core export; failing "
            "loudly instead of silently skipping."
        )
    assert len(OPERATIONS) >= 15


@pytest.mark.parametrize(
    "method,suffix", OPERATIONS, ids=[f"{m}_{s}" for m, s in OPERATIONS]
)
def test_operation_reachable_through_analytics_resource(method: str, suffix: str):
    assert_operation_covered(_client(), method, suffix)


# ── Fixture-based proofs -- ALWAYS run, independent of live spec
# availability, so the extraction + comparison logic itself is proven even
# when both SPEC_AVAILABLE paths are absent (mirrors mcp's AUD-0062
# parse_be_level_thresholds/find_first_existing fixture tests).


def test_extract_analytics_operations_ignores_unrelated_paths():
    fixture_spec = {
        "paths": {
            "/organizations/{orgId}/analytics": {"get": {}},
            "/organizations/{orgId}/analytics/events": {"get": {}, "post": {}},
            "/organizations/{orgId}/analytics/advanced/anomalies": {"get": {}},
            "/organizations/{orgId}/agents": {"get": {}},  # unrelated -- must be ignored
        }
    }
    # `.sort()` is order-irrelevant for the gate's actual pass/fail (each
    # operation is checked independently) -- assert the same SET.
    ops = extract_analytics_operations(fixture_spec)
    assert set(ops) == {
        ("GET", "/"),
        ("GET", "/advanced/anomalies"),
        ("GET", "/events"),
        ("POST", "/events"),
    }
    assert len(ops) == 4


def test_assert_operation_covered_fails_on_a_synthetic_unmapped_operation():
    with pytest.raises(pytest.fail.Exception, match="no AnalyticsResource coverage entry"):
        assert_operation_covered(_client(), "GET", "/advanced/new-thing-be-added")


def test_assert_operation_covered_fails_when_coverage_names_a_missing_method():
    saved = COVERAGE["GET /capture-state"]
    COVERAGE["GET /capture-state"] = ("this_method_does_not_exist", (), {})
    try:
        with pytest.raises(
            pytest.fail.Exception, match="has no method 'this_method_does_not_exist'"
        ):
            assert_operation_covered(_client(), "GET", "/capture-state")
    finally:
        COVERAGE["GET /capture-state"] = saved


class TestFindFirstExisting:
    """
    CI-layout path resolution, using the SAME candidate list used at
    runtime -- mirrors mcp's ``findFirstExisting — CI-layout path
    resolution`` (AUD-0062).
    """

    def test_resolves_the_local_monorepo_ui_swagger_json_layout(self, tmp_path: Path):
        ui_swagger = (tmp_path / "ui" / "swagger.json").resolve()
        ui_swagger.parent.mkdir(parents=True)
        ui_swagger.write_text("{}")
        sdk_python_tests_dir = tmp_path / "sdk-python" / "tests"
        sdk_python_tests_dir.mkdir(parents=True)

        assert (
            find_first_existing(sdk_python_tests_dir, ANALYTICS_SPEC_RELATIVE_CANDIDATES)
            == ui_swagger
        )

    def test_resolves_contract_drift_workspace_root_swagger_generated_json(self, tmp_path: Path):
        # Mirrors contract-drift.yml's real checkout shape: sdk-python + sdk
        # + be-core as siblings under one workspace root, with the freshly
        # exported spec landing at that same workspace root -- not a ui/ dir.
        generated = (tmp_path / "swagger.generated.json").resolve()
        generated.write_text("{}")
        sdk_python_tests_dir = tmp_path / "sdk-python" / "tests"
        sdk_python_tests_dir.mkdir(parents=True)

        assert (
            find_first_existing(sdk_python_tests_dir, ANALYTICS_SPEC_RELATIVE_CANDIDATES)
            == generated
        )

    def test_returns_none_when_neither_layout_is_present(self, tmp_path: Path):
        sdk_python_tests_dir = tmp_path / "sdk-python" / "tests"
        sdk_python_tests_dir.mkdir(parents=True)

        assert (
            find_first_existing(sdk_python_tests_dir, ANALYTICS_SPEC_RELATIVE_CANDIDATES) is None
        )
