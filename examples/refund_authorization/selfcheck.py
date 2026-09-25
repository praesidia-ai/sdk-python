"""
Offline self-check (no network): the installed SDK has every call refund.py makes, the env
parsing refuses what it must (exit 2), and a deny exits 3 without calling Stripe.
"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr
from importlib import metadata
from pathlib import Path
from types import SimpleNamespace

import praesidia
from praesidia import InteractionDeniedError
from praesidia.audit import AuditResource

import refund

ENV = {"PRAESIDIA_API_KEY": "selfcheck", "PRAESIDIA_ORG_ID": "org", "PRAESIDIA_AGENT_ID": "agent",
       "STRIPE_SECRET_KEY": "sk_test_selfcheck", "STRIPE_CHARGE_ID": "ch_selfcheck"}


def check(ok: bool, what: str) -> None:
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        sys.exit(1)


def denied(*args, **kwargs):
    raise InteractionDeniedError("agent_to_saas", "stripe.refund", "policy_denied", {})


def no_stripe(*args, **kwargs):
    raise AssertionError("Stripe was called on a deny")


source = Path(praesidia.__file__).resolve()
check(source.parent.parent.name in ("site-packages", "dist-packages"),
      f"installed praesidia {metadata.version('praesidia')} imported from {source.parent}")
check(all(hasattr(praesidia.PraesidiaInteractionHooks, m) for m in ("before_interaction", "report_outcome"))
      and all(hasattr(AuditResource, m) for m in ("get_decision_receipt", "request_package", "get_package",
                                                   "download_package")), "installed SDK has the SDK-0325/0327 calls")
check(refund.load_config(ENV).base_url == refund.PRAESIDIA_API, "env parsing: PRAESIDIA_BASE_URL defaults to production")
for override in ({"STRIPE_SECRET_KEY": ""}, {"STRIPE_SECRET_KEY": "sk_" + "live_x"}, {"PRAESIDIA_API_KEY": ""}):
    with redirect_stderr(io.StringIO()):
        code = refund.main({**ENV, **override})
    check(code == 2, f"refuses {override} with exit 2")
graph = SimpleNamespace(put_asset_by_external_id=lambda ext, data: {"id": ext},
                        put_relationship_by_external_id=lambda ext, data: {"id": ext})
code = refund.run(refund.load_config(ENV), client=SimpleNamespace(ai_systems=graph),
                  hooks=SimpleNamespace(before_interaction=denied), stripe=no_stripe, out=lambda *a: None)
check(code == 3, "deny exits 3 without calling Stripe")
print("selfcheck OK")
