"""SDK-0329: examples/refund_authorization (refusal paths, env parsing, no mock backend)."""
import importlib.util
import io
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from praesidia.exceptions import ForbiddenError, InteractionDeniedError, ServerError

EXAMPLE = Path(__file__).parents[1] / "examples/refund_authorization"
# SDK-0364: the example is excluded from the sdist, so skip only outside a Git checkout; in a
# checkout a deleted or renamed example must fail, since the INFRA-1303 acceptance chain relies on it.
if not (EXAMPLE.parents[1] / ".git").exists():
    pytest.skip("not a git checkout (e.g. an sdist without examples/refund_authorization)", allow_module_level=True)
assert EXAMPLE.is_dir(), f"{EXAMPLE} is missing (renamed or deleted?)"
spec = importlib.util.spec_from_file_location("refund_example", EXAMPLE / "refund.py")
refund = sys.modules["refund_example"] = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refund)

ENV = {
    "PRAESIDIA_API_KEY": "pk_example_not_real",
    "PRAESIDIA_ORG_ID": "11111111-1111-4111-8111-111111111111",
    "PRAESIDIA_AGENT_ID": "22222222-2222-4222-8222-222222222222",
    "STRIPE_SECRET_KEY": "sk_test_example_not_real",
    "STRIPE_CHARGE_ID": "ch_example",
}
DECISION = {"verdict": "allow", "reasonCode": "approval_consumed", "approvalId": "ap_1", "policyFingerprint": "f",
            "ttlSeconds": 0, "enforcementMode": "enforce", "decisionId": "d_1"}


class Graph:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def put_asset_by_external_id(self, external_id, data):
        self.calls.append(external_id)
        if self.fail:
            raise self.fail
        return {"id": f"asset-{len(self.calls)}"}

    def put_relationship_by_external_id(self, external_id, data):
        self.calls.append(external_id)
        return {"id": "rel"}


class Audit:
    def __init__(self, package=b"PK", effective_to=()):
        self.package, self.effective_to, self.calls = package, list(effective_to), []

    def get_decision_receipt(self, decision_id):
        return {"decisionId": decision_id}

    def export_bundle(self, *, from_date, to_date, include_unrooted=False):
        self.calls.append(("bundle", from_date, to_date, include_unrooted))
        bundle = type("B", (bytes,), {})(b"PK")  # the server keeps answering its last cut
        bundle.effective_to = self.effective_to.pop(0) if len(self.effective_to) > 1 else self.effective_to[0]
        return bundle

    def request_package(self):
        self.calls.append(("request",))
        return {"id": "p1", "status": "queued"}

    def get_package(self, package_id):
        return {"id": package_id, "status": "done"}

    def download_package(self, package_id):
        return self.package


class Client:
    def __init__(self, graph=None, audit=None):
        self.ai_systems, self.audit = graph or Graph(), audit or Audit()


class Hooks:
    def __init__(self, decision=None, deny=None):
        self.decision, self.deny, self.outcomes = decision, deny, []

    def before_interaction(self, interaction_type, action, *, fail_mode="closed"):
        assert (interaction_type, action["name"], fail_mode) == ("agent_to_saas", "stripe.refund", "closed")
        assert action["arguments"] == {"amount": 8250, "currency": "EUR", "charge": "ch_example"}
        if self.deny:
            raise InteractionDeniedError("agent_to_saas", "stripe.refund", self.deny, {})
        return type("R", (), {"decision": self.decision})()

    def report_outcome(self, approval_id, status, **kwargs):
        self.outcomes.append((approval_id, status, kwargs))
        return {"approvalId": approval_id, "decisionId": "d_out"}


def never(*args, **kwargs):
    raise AssertionError("Stripe must not be called")


def run(hooks, stripe=never, tmp_path=None):
    cfg = refund.load_config(ENV)
    return refund.run(cfg, client=Client(), hooks=hooks, stripe=stripe, out=lambda *a: None,
                      sleep=lambda s: None, package_path=(tmp_path or Path(".")) / "audit-package.zip")


def test_refund_example_env_parsing():
    cfg = refund.load_config(ENV)
    assert (cfg.charge, cfg.base_url) == ("ch_example", "https://api.praesidia.ai")
    assert refund.load_config({**ENV, "PRAESIDIA_BASE_URL": "https://eu.example"}).base_url == "https://eu.example"


@pytest.mark.parametrize("override", [{"STRIPE_SECRET_KEY": ""}, {"STRIPE_SECRET_KEY": "sk_live_x"},
                                      {"STRIPE_SECRET_KEY": "rk_test_x"}, {"PRAESIDIA_AGENT_ID": ""},
                                      {"STRIPE_CHARGE_ID": ""}])
def test_refund_example_bad_config_exits_2_before_any_client(override):
    assert refund.main({**ENV, **override}) == 2


@pytest.mark.parametrize("reason", ["policy_denied", "approval_rejected", "approval_wait_timeout"])
def test_refund_example_deny_exits_3_without_stripe(reason):
    assert run(Hooks(deny=reason)) == 3


@pytest.mark.parametrize("change", [{"enforcementMode": "observe"}, {"approvalId": None}])
def test_refund_example_ungoverned_allow_exits_2_without_stripe(change):
    assert run(Hooks({**DECISION, **change})) == 2


def test_refund_example_allow_refunds_once_with_approval_idempotency_key(tmp_path):
    seen = []

    def stripe(key, charge, idempotency_key):
        seen.append((key, charge, idempotency_key))
        return {"id": "re_1", "status": "succeeded"}

    hooks = Hooks(DECISION)
    assert run(hooks, stripe, tmp_path) == 0
    assert seen == [("sk_test_example_not_real", "ch_example", "ap_1")]
    assert hooks.outcomes == [("ap_1", "succeeded", {"result": {"id": "re_1", "status": "succeeded"},
                                                    "target_system": "stripe", "target_transaction_id": "re_1"})]
    assert (tmp_path / "audit-package.zip").read_bytes() == b"PK"


@pytest.mark.parametrize("status, outcome", [(402, "failed_no_effect"), (503, "unknown")])
def test_refund_example_stripe_failure_reports_outcome_exits_1(status, outcome):
    def stripe(key, charge, idempotency_key):
        request = httpx.Request("POST", refund.STRIPE_API + "/v1/refunds")
        raise httpx.HTTPStatusError("x", request=request, response=httpx.Response(status, request=request))

    hooks = Hooks(DECISION)
    assert run(hooks, stripe) == 1
    assert [o[:2] for o in hooks.outcomes] == [("ap_1", outcome)]


def test_refund_example_stripe_request_shape():
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={"id": "re_1"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        assert refund.stripe_refund("sk_test_x", "ch_1", "ap_1", http=http) == {"id": "re_1"}
    (req,) = sent
    assert (str(req.url), req.headers["Idempotency-Key"]) == ("https://api.stripe.com/v1/refunds", "ap_1")
    assert req.content == b"charge=ch_1&amount=825000&metadata%5Bpraesidia_approval_id%5D=ap_1"


def test_refund_example_has_no_mock_backend():
    assert (refund.PRAESIDIA_API, refund.STRIPE_API) == ("https://api.praesidia.ai", "https://api.stripe.com")
    for name in ("refund.py", "selfcheck.py", ".env.example", "requirements.txt"):
        text = (EXAMPLE / name).read_text().lower()
        for marker in ("localhost", "127.0.0.1", "respx", "mocktransport", "file:", "-e "):
            assert marker not in text, (name, marker)
    assert (EXAMPLE / "requirements.txt").read_text().split() == ["praesidia>=0.5.0"]


# SDK-0342: coverage of the refund by the package, --wait-rooted, the verify command, a 403 on step 1.
NOW = datetime(2026, 9, 25, 10, 17, tzinfo=UTC).timestamp()  # frozen clock: refunded_at
HOUR_END = "2026-09-25T11:00:00.000Z"  # the refund's hour end, as refund.py computes it
PAST, FUTURE = "2026-01-01T00:00:00.000Z", "2999-01-01T00:00:00.000Z"


def iso(ts):
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def package(to, clamp):
    """A package ZIP whose verification.txt carries be's buildVerificationTxt window lines."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("verification.txt", f"Praesidia Audit Package\nEvidence range: {PAST} .. {to}\n"
                                       f"Requested range end: {iso(NOW)}\nRange end clamp: {clamp}\n")
    return buf.getvalue()


def approved_run(tmp_path, audit=None, graph=None, env=None, **kwargs):
    lines, hooks = [], Hooks(DECISION)
    client = Client(graph, audit)
    code = refund.run(refund.load_config({**ENV, **(env or {})}), client=client, hooks=hooks,
                      stripe=lambda *a: {"id": "re_1"}, out=lambda *a: lines.append(" ".join(map(str, a))),
                      sleep=lambda s: None, package_path=tmp_path / "audit-package.zip", clock=lambda: NOW, **kwargs)
    return code, lines, client, hooks


def starts(lines, prefix):
    return [line for line in lines if line.startswith(prefix)]


def test_refund_example_403_on_inventory_still_reaches_the_decision(tmp_path):
    code, lines, _, hooks = approved_run(tmp_path, graph=Graph(fail=ForbiddenError("forbidden")))
    assert code == 0 and [o[:2] for o in hooks.outcomes] == [("ap_1", "succeeded")]
    assert "graph: mapping skipped (403). The API key lacks the ai-systems:write scope; add it to the key (README.md)" in lines


def test_refund_example_other_inventory_errors_still_raise(tmp_path):
    with pytest.raises(ServerError):
        approved_run(tmp_path, graph=Graph(fail=ServerError("boom", 500)))


def test_refund_example_clamp_before_the_refund_prints_not_yet_covered(tmp_path):
    code, lines, _, _ = approved_run(tmp_path, Audit(package("2026-09-25T10:00:00.000Z", "clamped_to_last_rooted_hour")))
    assert code == 0 and not starts(lines, "refund covered: ")
    (line,) = starts(lines, "refund not yet covered: ")
    assert line == ("refund not yet covered: the package's evidence ends at 2026-09-25T10:00:00.000Z "
                    "(clamp clamped_to_last_rooted_hour); the refund was at 2026-09-25T10:17:00.000Z. Its rows are "
                    f"covered once the hour ending {HOUR_END} is Merkle-rooted (hourly, just after that hour closes). "
                    "Request a new audit package after then, or pass --wait-rooted next time")


def test_refund_example_window_end_is_exclusive(tmp_path):
    _, lines, _, _ = approved_run(tmp_path, Audit(package(iso(NOW), "clamped_to_last_rooted_hour")))
    assert starts(lines, "refund not yet covered: ")
    _, lines, _, _ = approved_run(tmp_path, Audit(package(iso(NOW + 0.001), "clamped_to_last_rooted_hour")))
    assert starts(lines, "refund covered: ") == [
        "refund covered: the package's evidence ends at 2026-09-25T10:17:00.001Z (clamp clamped_to_last_rooted_hour), "
        "after the refund at 2026-09-25T10:17:00.000Z"]


@pytest.mark.parametrize("clamp", ["none", "clamped_to_last_rooted_hour", "no_rooted_hour", "clamped_to_unrooted_gap"])
def test_refund_example_known_clamp_after_the_refund_is_covered(tmp_path, clamp):
    _, lines, _, _ = approved_run(tmp_path, Audit(package(FUTURE, clamp)))
    assert starts(lines, "refund covered: ") and not starts(lines, "refund not yet covered: ")


def test_refund_example_unknown_clamp_or_unreadable_package_is_not_covered(tmp_path):
    _, lines, _, _ = approved_run(tmp_path, Audit(package(FUTURE, "include_unrooted")))
    assert "unknown clamp reason include_unrooted" in starts(lines, "refund not yet covered: ")[0]
    _, lines, _, _ = approved_run(tmp_path, Audit(b"PK"))
    assert "range could not be read from its verification.txt" in starts(lines, "refund not yet covered: ")[0]


def test_refund_example_verify_command_carries_the_platform_key(tmp_path):
    _, lines, _, _ = approved_run(tmp_path)
    path = tmp_path / "audit-package.zip"
    assert f"verify offline: npx @praesidia/audit-verifier {path} --platform-key <platform-key.pem> " \
           "--platform-key-fingerprint <sha256hex> --summary" in lines
    assert starts(lines, "platform key: ")
    _, lines, _, _ = approved_run(tmp_path, env={"PRAESIDIA_PLATFORM_KEY_FILE": "k.pem",
                                                 "PRAESIDIA_PLATFORM_KEY_FINGERPRINT": "ab" * 32})
    assert f"verify offline: npx @praesidia/audit-verifier {path} --platform-key k.pem " \
           f"--platform-key-fingerprint {'ab' * 32} --summary" in lines
    assert not starts(lines, "platform key: ")
    for only in ({"PRAESIDIA_PLATFORM_KEY_FILE": "k.pem"}, {"PRAESIDIA_PLATFORM_KEY_FINGERPRINT": "ab" * 32}):
        _, lines, _, _ = approved_run(tmp_path, env=only)
        assert starts(lines, "platform key: ")


def test_refund_example_wait_rooted_counts_effective_to_equal_to_the_hour_end_as_rooted(tmp_path):
    audit = Audit(package(HOUR_END, "none"), effective_to=[PAST, HOUR_END])  # be clamps effectiveTo to `to`
    _, lines, _, _ = approved_run(tmp_path, audit, wait_rooted=True)
    probes = [c for c in audit.calls if c[0] == "bundle"]
    assert probes == [("bundle", "2026-09-25T10:00:00.000Z", HOUR_END, False)] * 2
    assert audit.calls.index(("request",)) == 2  # the package is requested after the root
    assert f"--wait-rooted: rooted through {HOUR_END}" in lines and starts(lines, "refund covered: ")


def test_refund_example_wait_rooted_is_bounded(tmp_path):
    audit = Audit(package(PAST, "clamped_to_last_rooted_hour"), effective_to=[PAST])
    code, lines, _, _ = approved_run(tmp_path, audit, wait_rooted=True)
    assert code == 0 and len([c for c in audit.calls if c[0] == "bundle"]) == 41
    assert "--wait-rooted: not rooted after 80 min; requesting the package anyway" in lines
    assert ("request",) in audit.calls and starts(lines, "refund not yet covered: ")


def test_refund_example_wait_rooted_without_the_header_does_not_wait(tmp_path):
    audit = Audit(package(FUTURE, "none"), effective_to=[None])
    _, lines, _, _ = approved_run(tmp_path, audit, wait_rooted=True)
    assert len([c for c in audit.calls if c[0] == "bundle"]) == 1
    assert "--wait-rooted: this server does not report the rooted window; not waiting" in lines
