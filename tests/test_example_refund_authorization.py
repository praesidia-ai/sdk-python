"""SDK-0329: examples/refund_authorization (refusal paths, env parsing, no mock backend)."""
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

from praesidia.exceptions import InteractionDeniedError

EXAMPLE = Path(__file__).parents[1] / "examples/refund_authorization"
if not EXAMPLE.is_dir():  # the example is excluded from the sdist
    pytest.skip("examples/refund_authorization not present", allow_module_level=True)
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
    def __init__(self):
        self.calls = []

    def put_asset_by_external_id(self, external_id, data):
        self.calls.append(external_id)
        return {"id": f"asset-{len(self.calls)}"}

    def put_relationship_by_external_id(self, external_id, data):
        self.calls.append(external_id)
        return {"id": "rel"}


class Audit:
    def get_decision_receipt(self, decision_id):
        return {"decisionId": decision_id}

    def request_package(self):
        return {"id": "p1", "status": "queued"}

    def get_package(self, package_id):
        return {"id": package_id, "status": "done"}

    def download_package(self, package_id):
        return b"PK"


class Client:
    def __init__(self):
        self.ai_systems, self.audit = Graph(), Audit()


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
