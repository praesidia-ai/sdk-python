"""
Governed EUR 8,250 Stripe refund (test mode) through Praesidia. See README.md.

Exit codes: 0 refunded, 1 error, 2 bad configuration (e.g. STRIPE_SECRET_KEY missing or not
sk_test_...), 3 Praesidia did not authorize the refund (deny, rejected or timed-out approval).
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

import httpx

from praesidia import InteractionDeniedError, Praesidia, PraesidiaInteractionHooks

PRAESIDIA_API = "https://api.praesidia.ai"
STRIPE_API = "https://api.stripe.com"
AMOUNT_EUR = 8250
PACKAGE_TIMEOUT_S = 300.0


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    api_key: str
    org_id: str
    agent_id: str
    base_url: str
    stripe_key: str
    charge: str


def load_config(env: Mapping[str, str]) -> Config:
    """Read the environment. Refuses anything but a Stripe test-mode secret key."""
    names = ("PRAESIDIA_API_KEY", "PRAESIDIA_ORG_ID", "PRAESIDIA_AGENT_ID", "STRIPE_SECRET_KEY", "STRIPE_CHARGE_ID")
    missing = [n for n in names if not env.get(n, "").strip()]
    if missing:
        raise ConfigError(f"missing environment variable(s): {', '.join(missing)} (see .env.example)")
    if not env["STRIPE_SECRET_KEY"].startswith("sk_test_"):
        raise ConfigError("STRIPE_SECRET_KEY must be a Stripe test-mode secret key (sk_test_...); refusing")
    return Config(env["PRAESIDIA_API_KEY"], env["PRAESIDIA_ORG_ID"], env["PRAESIDIA_AGENT_ID"],
                  env.get("PRAESIDIA_BASE_URL") or PRAESIDIA_API, env["STRIPE_SECRET_KEY"], env["STRIPE_CHARGE_ID"])


def stripe_refund(stripe_key: str, charge: str, idempotency_key: str, *, http: Optional[httpx.Client] = None) -> Any:
    """POST /v1/refunds. The approval id is the Idempotency-Key: a retry cannot refund twice at Stripe."""
    request = dict(
        auth=(stripe_key, ""),
        headers={"Idempotency-Key": idempotency_key},
        data={"charge": charge, "amount": str(AMOUNT_EUR * 100), "metadata[praesidia_approval_id]": idempotency_key},
    )
    response = (http or httpx).post(f"{STRIPE_API}/v1/refunds", timeout=30.0, **request)
    response.raise_for_status()
    return response.json()


def map_graph(client: Any, agent_id: str) -> None:
    """Agent -CALLS-> Stripe in the asset graph. External-id upserts: a re-run changes nothing."""
    graph = client.ai_systems
    agent = graph.put_asset_by_external_id(f"refund-example:agent:{agent_id}",
                                           {"name": f"Refund agent {agent_id}", "assetType": "AGENT", "source": "api"})
    stripe = graph.put_asset_by_external_id("refund-example:stripe",
                                            {"name": "Stripe Refunds API", "assetType": "API", "source": "api"})
    graph.put_relationship_by_external_id(f"refund-example:{agent_id}:calls:stripe", {
        "sourceAssetId": agent["id"], "targetAssetId": stripe["id"], "relationshipType": "CALLS", "source": "api"})


def run(cfg: Config, *, client: Any, hooks: Any, stripe: Callable[..., Any] = stripe_refund,
        out: Callable[..., None] = print, sleep: Callable[[float], None] = time.sleep,
        package_path: Path = Path("audit-package.zip")) -> int:
    map_graph(client, cfg.agent_id)
    action = {"name": "stripe.refund", "arguments": {"amount": AMOUNT_EUR, "currency": "EUR", "charge": cfg.charge}}
    try:  # on require_approval the SDK polls until a human decides (approval_timeout)
        decision = hooks.before_interaction("agent_to_saas", action, fail_mode="closed").decision
    except InteractionDeniedError as denied:
        out(f"Not authorized by Praesidia ({denied.reason_code}); Stripe was not called.")
        return 3
    approval_id = decision.get("approvalId")
    if decision["enforcementMode"] != "enforce" or not approval_id:
        out(f"Allowed without an approval (mode {decision['enforcementMode']}): the org must be in enforce mode "
            "with the STEP_UP policy from README.md. Stripe was not called.")
        return 2
    try:
        refund = stripe(cfg.stripe_key, cfg.charge, approval_id)
    except (httpx.HTTPError, ValueError) as err:
        code = getattr(getattr(err, "response", None), "status_code", 0)
        hooks.report_outcome(approval_id, "failed_no_effect" if 400 <= code < 500 else "unknown", target_system="stripe")
        out(f"Stripe refund failed: {err}")
        return 1
    outcome = hooks.report_outcome(approval_id, "succeeded", result=refund, target_system="stripe",
                                   target_transaction_id=refund["id"])
    out(f"Refunded {refund['id']} (approval {approval_id}, outcome decision {outcome['decisionId']})")
    out(json.dumps(client.audit.get_decision_receipt(decision["decisionId"]), indent=2))
    job = client.audit.request_package()
    deadline = time.monotonic() + PACKAGE_TIMEOUT_S
    while job["status"] not in ("done", "failed") and time.monotonic() < deadline:
        sleep(2.0)
        job = client.audit.get_package(job["id"])
    if job["status"] != "done":
        out(f"Audit package {job['id']} not ready: {job['status']} {job.get('error') or ''}")
        return 1
    package_path.write_bytes(client.audit.download_package(job["id"]))
    out(f"Verify offline: praesidia-verify {package_path}")
    return 0


def main(env: Optional[Mapping[str, str]] = None) -> int:
    try:
        cfg = load_config(os.environ if env is None else env)
    except ConfigError as err:
        print(f"refund.py: {err}", file=sys.stderr)
        return 2

    def waiting(pending: Mapping[str, Any]) -> None:
        print(f"Approval required: approve {pending['approvalId']} in Monitor > Governance > Approvals")

    client = Praesidia(api_key=cfg.api_key, org_id=cfg.org_id, base_url=cfg.base_url)
    with PraesidiaInteractionHooks(api_key=cfg.api_key, org_id=cfg.org_id, agent_id=cfg.agent_id,
                                   base_url=cfg.base_url, on_approval_required=waiting) as hooks:
        return run(cfg, client=client, hooks=hooks)


if __name__ == "__main__":
    sys.exit(main())
