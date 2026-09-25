"""
Governed EUR 8,250 Stripe refund (test mode) through Praesidia. See README.md.

Exit codes: 0 refunded, 1 error, 2 bad configuration (e.g. STRIPE_SECRET_KEY missing or not
sk_test_...), 3 Praesidia did not authorize the refund (deny, rejected or timed-out approval).
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

import httpx

from praesidia import ForbiddenError, InteractionDeniedError, Praesidia, PraesidiaInteractionHooks

PRAESIDIA_API = "https://api.praesidia.ai"
STRIPE_API = "https://api.stripe.com"
AMOUNT_EUR = 8250
PACKAGE_TIMEOUT_S = 300.0
HOUR_S = 3600
# be roots each complete hour at :00 (merkle-root.service.ts, EVERY_HOUR); 80 min bounds the wait.
WAIT_ROOTED_S = 80 * 60
ROOT_POLL_S = 2 * 60
KNOWN_CLAMPS = ("none", "clamped_to_last_rooted_hour", "no_rooted_hour", "clamped_to_unrooted_gap")


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
    platform_key_path: str = ""
    platform_key_fingerprint: str = ""


def load_config(env: Mapping[str, str]) -> Config:
    """Read the environment. Refuses anything but a Stripe test-mode secret key."""
    names = ("PRAESIDIA_API_KEY", "PRAESIDIA_ORG_ID", "PRAESIDIA_AGENT_ID", "STRIPE_SECRET_KEY", "STRIPE_CHARGE_ID")
    missing = [n for n in names if not env.get(n, "").strip()]
    if missing:
        raise ConfigError(f"missing environment variable(s): {', '.join(missing)} (see .env.example)")
    if not env["STRIPE_SECRET_KEY"].startswith("sk_test_"):
        raise ConfigError("STRIPE_SECRET_KEY must be a Stripe test-mode secret key (sk_test_...); refusing")
    return Config(env["PRAESIDIA_API_KEY"], env["PRAESIDIA_ORG_ID"], env["PRAESIDIA_AGENT_ID"],
                  env.get("PRAESIDIA_BASE_URL") or PRAESIDIA_API, env["STRIPE_SECRET_KEY"], env["STRIPE_CHARGE_ID"],
                  env.get("PRAESIDIA_PLATFORM_KEY_FILE", ""), env.get("PRAESIDIA_PLATFORM_KEY_FINGERPRINT", ""))


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


def map_graph(client: Any, agent_id: str, out: Callable[..., None] = print) -> None:
    """Agent -CALLS-> Stripe in the asset graph. External-id upserts: a re-run changes nothing.

    Inventory, not the control: a key without ai-systems:write (403) skips it and the run continues.
    """
    try:
        _put_graph(client.ai_systems, agent_id)
    except ForbiddenError:
        out("graph: mapping skipped (403). The API key lacks the ai-systems:write scope; add it to the key (README.md)")


def _put_graph(graph: Any, agent_id: str) -> None:
    agent = graph.put_asset_by_external_id(f"refund-example:agent:{agent_id}",
                                           {"name": f"Refund agent {agent_id}", "assetType": "AGENT", "source": "api"})
    stripe = graph.put_asset_by_external_id("refund-example:stripe",
                                            {"name": "Stripe Refunds API", "assetType": "API", "source": "api"})
    graph.put_relationship_by_external_id(f"refund-example:{agent_id}:calls:stripe", {
        "sourceAssetId": agent["id"], "targetAssetId": stripe["id"], "relationshipType": "CALLS", "source": "api"})


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _ts(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def read_package_window(package: bytes) -> Optional[dict]:
    """The evidence window be wrote into the package's verification.txt (the package job response
    carries none): ``{"to", "requested_to", "clamp_reason"}``, or None when it cannot be read."""
    try:
        text = zipfile.ZipFile(io.BytesIO(package)).read("verification.txt").decode()
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError):
        return None

    def field(label: str) -> Optional[str]:
        match = re.search(rf"^{label}: (.+)$", text, re.M)
        return match.group(1).strip() if match else None

    evidence_range = (field("Evidence range") or "").split(" .. ")
    if len(evidence_range) != 2:
        return None
    return {"to": evidence_range[1], "requested_to": field("Requested range end"), "clamp_reason": field("Range end clamp")}


def report_coverage(window: Optional[dict], refunded_at: float, out: Callable[..., None]) -> None:
    """Only a known clamp reason whose (exclusive) rooted end is after the refund counts as covered."""
    at = _iso(refunded_at)
    known = bool(window) and window["clamp_reason"] in KNOWN_CLAMPS
    try:
        after = known and _ts(window["to"]) > refunded_at
    except ValueError:
        after = False
    if after:
        out(f"refund covered: the package's evidence ends at {window['to']} (clamp {window['clamp_reason']}), "
            f"after the refund at {at}")
        return
    hour_end = _iso((refunded_at // HOUR_S + 1) * HOUR_S)
    if not window:
        seen = "range could not be read from its verification.txt"
    else:
        reason = window["clamp_reason"]
        seen = f"ends at {window['to']} (clamp {reason}{'' if known else f': unknown clamp reason {reason}'})"
    out(f"refund not yet covered: the package's evidence {seen}; the refund was at {at}. "
        f"Its rows are covered once the hour ending {hour_end} is Merkle-rooted (hourly, just after that hour closes). "
        "Request a new audit package after then, or pass --wait-rooted next time")


def wait_for_root(audit: Any, refunded_at: float, out: Callable[..., None], sleep: Callable[[float], None]) -> None:
    """--wait-rooted: probe the refund's hour as a signed bundle until be's rooted end reaches it.

    be clamps effective_to to the requested ``to``, so a rooted hour answers exactly its end (>=)."""
    end = (refunded_at // HOUR_S + 1) * HOUR_S
    start_iso, end_iso = _iso(end - HOUR_S), _iso(end)
    out(f"--wait-rooted: waiting for the hour ending {end_iso} to be Merkle-rooted (at most {WAIT_ROOTED_S // 60} min)")
    for i in range(WAIT_ROOTED_S // ROOT_POLL_S + 1):
        if i:
            sleep(ROOT_POLL_S)
        effective_to = audit.export_bundle(from_date=start_iso, to_date=end_iso, include_unrooted=False).effective_to
        if not effective_to:
            out("--wait-rooted: this server does not report the rooted window; not waiting")
            return
        if _ts(effective_to) >= end:
            out(f"--wait-rooted: rooted through {effective_to}")
            return
    out(f"--wait-rooted: not rooted after {WAIT_ROOTED_S // 60} min; requesting the package anyway")


def run(cfg: Config, *, client: Any, hooks: Any, stripe: Callable[..., Any] = stripe_refund,
        out: Callable[..., None] = print, sleep: Callable[[float], None] = time.sleep,
        package_path: Path = Path("audit-package.zip"), wait_rooted: bool = False,
        clock: Callable[[], float] = time.time) -> int:
    map_graph(client, cfg.agent_id, out)
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
    refunded_at = clock()  # after report_outcome: the refund's decision + outcome rows exist
    out(f"Refunded {refund['id']} (approval {approval_id}, outcome decision {outcome['decisionId']})")
    out(json.dumps(client.audit.get_decision_receipt(decision["decisionId"]), indent=2))
    if wait_rooted:
        wait_for_root(client.audit, refunded_at, out, sleep)
    job = client.audit.request_package()
    deadline = time.monotonic() + PACKAGE_TIMEOUT_S
    while job["status"] not in ("done", "failed") and time.monotonic() < deadline:
        sleep(2.0)
        job = client.audit.get_package(job["id"])
    if job["status"] != "done":
        out(f"Audit package {job['id']} not ready: {job['status']} {job.get('error') or ''}")
        return 1
    package = client.audit.download_package(job["id"])
    package_path.write_bytes(package)
    out(f"audit package: {package_path}")
    report_coverage(read_package_window(package), refunded_at, out)
    # The verifier embeds no platform key yet (VERIFIER-RELEASE.md section 6); without one a real package fails `signature`.
    out(f"verify offline: npx @praesidia/audit-verifier {package_path} --platform-key "
        f"{cfg.platform_key_path or '<platform-key.pem>'} --platform-key-fingerprint "
        f"{cfg.platform_key_fingerprint or '<sha256hex>'} --summary")
    if not (cfg.platform_key_path and cfg.platform_key_fingerprint):
        out("platform key: get the Praesidia platform public key (PEM) and its SHA-256 fingerprint from Praesidia over a "
            "channel independent of this package (none is published yet), then set PRAESIDIA_PLATFORM_KEY_FILE and "
            "PRAESIDIA_PLATFORM_KEY_FINGERPRINT")
    return 0


def main(env: Optional[Mapping[str, str]] = None, argv: Optional[list] = None) -> int:
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
        return run(cfg, client=client, hooks=hooks, wait_rooted="--wait-rooted" in (sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    sys.exit(main())
