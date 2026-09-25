# Governed refund: EUR 8,250 through Stripe test mode

A standalone example that installs `praesidia` as a package (no checkout, no mock backend). An
agent asks Praesidia before it refunds EUR 8,250 through Stripe. A human approves. The refund
runs once. You get a Decision Receipt and an audit package you can verify offline. The TypeScript
twin is `@praesidia/sdk`'s `examples/refund-authorization/`.

Time to first run: the 10-minute claim is **not yet measured**.

## What `refund.py` does

1. Maps agent -CALLS-> Stripe in the asset graph with external-id upserts, so a re-run changes nothing.
   A key without `ai-systems:write` gets a 403 here; the script logs a hint and continues.
2. Asks `before_interaction("agent_to_saas", {"name": "stripe.refund", "arguments": {"amount": 8250,
   "currency": "EUR", "charge": ...}}, fail_mode="closed")`.
3. On `require_approval` it prints the approval id and waits. The SDK polls for up to 10 minutes.
4. On `allow` it calls Stripe `POST /v1/refunds` with `Idempotency-Key: <approvalId>`. A retry of
   the same approval cannot refund twice at Stripe. A new run needs a new approval.
5. Records the result with `report_outcome(approval_id, "succeeded", result=refund,
   target_system="stripe", target_transaction_id=refund["id"])`.
6. Prints `audit.get_decision_receipt(decisionId)`, then requests and downloads the audit
   package to `./audit-package.zip`, says whether the package covers the refund, and prints
   the offline verify command.

| Exit | Meaning |
|---|---|
| 0 | Refunded, outcome recorded, package downloaded |
| 1 | Error (Stripe failure, which is reported as outcome `failed_no_effect` or `unknown`; package not ready) |
| 2 | Bad configuration: a missing variable, `STRIPE_SECRET_KEY` missing or not `sk_test_...`, or an allow without an approval (org not in `enforce`, or the policy is missing) |
| 3 | Praesidia did not authorize it (deny, rejected, expired or timed-out approval). Stripe is not called |

## Install

This example needs `praesidia>=0.5.0`. **0.5.0 is not yet published.** Until it is, build a wheel
from the `sdk-python` source and install that wheel instead of `requirements.txt`:

```bash
python -m venv .venv && . .venv/bin/activate
(cd /path/to/sdk-python && python -m build --wheel)     # needs `pip install build`
pip install /path/to/sdk-python/dist/praesidia-*.whl    # once 0.5.0 is out: pip install -r requirements.txt
python selfcheck.py                                     # offline: no Praesidia or Stripe call
```

A wheel built before the release bump reports version 0.4.1 but has every call this example uses.
`selfcheck.py` checks for those calls, not for the version number.

## Configure

Copy `.env.example` to `.env` (git-ignored) and export it (`set -a; . ./.env; set +a`).

| Variable | Where to get it |
|---|---|
| `PRAESIDIA_API_KEY` | One organization API key. App: Configure > Integrations > API keys. Scopes **`ai-systems:write`** (step 1, asset graph), **`agents:invoke`** (decision + outcome) and **`audit:read`** (receipt, package, `--wait-rooted`). The key is shown once. |
| `PRAESIDIA_ORG_ID` | Your organization id (see the SDK docs' Credentials section) |
| `PRAESIDIA_AGENT_ID` | The id of the registered agent that performs the refund (Agents page) |
| `PRAESIDIA_BASE_URL` | Optional. Defaults to `https://api.praesidia.ai` |
| `STRIPE_SECRET_KEY` | Stripe dashboard, test mode: a `sk_test_...` key. Any other key is refused (exit 2) |
| `STRIPE_CHARGE_ID` | A test-mode charge (`ch_...`) of exactly EUR 8,250.00. Once it is fully refunded, Stripe refuses a second refund |
| `PRAESIDIA_PLATFORM_KEY_FILE` | Optional. The Praesidia platform public key (PEM), filled into the printed verify command. From Praesidia, over a channel independent of the package (see Run) |
| `PRAESIDIA_PLATFORM_KEY_FINGERPRINT` | Optional. The SHA-256 of that key's SPKI DER (64 hex characters). Same channel as the key, never from the package itself |

**Step 1 and the key's scopes.** Step 1 calls `PUT .../ai-assets/by-external-id/:externalId`
(twice) and `PUT .../asset-relationships/by-external-id/:externalId`. They take the
`ai-systems:write` scope, and the organization needs the AI Systems feature. A key without that
scope gets a 403. `refund.py` then prints
`graph: mapping skipped (403). The API key lacks the ai-systems:write scope` and continues,
because the mapping is inventory and not the control. Any other error still stops the run.
Unlike the TypeScript twin, there is no `PRAESIDIA_INVENTORY_API_KEY` override for servers that
predate `ai-systems:write`.

The org must be in **`enforce`** governance mode. It defaults to `observe`. In observe a STEP_UP
policy returns `allow` and no approval is created, so `refund.py` stops with exit 2 and does not
call Stripe. Setting the mode is currently a platform-admin action.

## Policy

Decisions are default-deny. Add two rules to the agent with
`POST /organizations/{orgId}/agents/{agentId}/tool-policies`, one body per rule. The first sends
refunds over EUR 5,000 to a human. The second allows smaller ones.

```json
{"toolPattern": "agent_to_saas.stripe.refund", "mode": "STEP_UP", "priority": 10,
 "conditions": {"all": [{"path": "amount", "op": "gt", "value": 5000}]}}
```

```json
{"toolPattern": "agent_to_saas.stripe.refund", "mode": "ALLOW", "priority": 20}
```

## Run

```bash
python refund.py
```

Approve the printed approval id in Monitor > Governance > Approvals. The script then refunds,
records the outcome, prints the Decision Receipt and writes `./audit-package.zip`.

**Does the package cover the refund?** Praesidia cuts every audit package at the end of the last
Merkle-rooted hour, so each row in it carries an inclusion proof. Hours are rooted once they
close, on the hour. A package requested seconds after the refund therefore ends before the
refund's own decision and outcome rows. The script reads the cut from the package's
`verification.txt` (`Evidence range`, `Range end clamp`) and prints one of:

```text
refund covered: the package's evidence ends at <to> (clamp <reason>), after the refund at <refundedAt>
refund not yet covered: the package's evidence ends at <to> (clamp <reason>); the refund was at <refundedAt>. Its rows are covered once the hour ending <hourEnd> is Merkle-rooted (hourly, just after that hour closes). Request a new audit package after then, or pass --wait-rooted next time
```

Known clamp reasons are `none`, `clamped_to_last_rooted_hour`, `clamped_to_unrooted_gap` and
`no_rooted_hour`. Any other reason is reported as not yet covered. To wait for the root before
the package is requested, run `python refund.py --wait-rooted`. The script then checks the
refund's hour every 2 minutes, for at most 80 minutes, through the signed-bundle route
(`audit.export_bundle`, its `effective_to`). It never creates a root. If the hour is still not
rooted, it requests the package anyway and says so.

Verify the package offline with the audit verifier (`praesidia-verify`, needs Node.js):

```bash
npx @praesidia/audit-verifier ./audit-package.zip --platform-key <platform-key.pem> \
  --platform-key-fingerprint <sha256hex> --summary
```

The verifier does not embed a Praesidia platform key yet. Without `--platform-key`, a real package
prints `FAIL signature` and exits 1. Get the key and its fingerprint from Praesidia over a channel
independent of the package: no public channel is published yet. Set `PRAESIDIA_PLATFORM_KEY_FILE`
and `PRAESIDIA_PLATFORM_KEY_FINGERPRINT` and the script prints the command filled in. The
verifier's sample key is not a Praesidia key. Never use it for a real package.

This directory is excluded from the published wheel and sdist.
