# Governed refund: EUR 8,250 through Stripe test mode

A standalone example that installs `praesidia` as a package (no checkout, no mock backend). An
agent asks Praesidia before it refunds EUR 8,250 through Stripe. A human approves. The refund
runs once. You get a Decision Receipt and an audit package you can verify offline. The TypeScript
twin is `@praesidia/sdk`'s `examples/refund-authorization/`.

Time to first run: the 10-minute claim is **not yet measured**.

## What `refund.py` does

1. Maps agent -CALLS-> Stripe in the asset graph with external-id upserts, so a re-run changes nothing.
2. Asks `before_interaction("agent_to_saas", {"name": "stripe.refund", "arguments": {"amount": 8250,
   "currency": "EUR", "charge": ...}}, fail_mode="closed")`.
3. On `require_approval` it prints the approval id and waits. The SDK polls for up to 10 minutes.
4. On `allow` it calls Stripe `POST /v1/refunds` with `Idempotency-Key: <approvalId>`. A retry of
   the same approval cannot refund twice at Stripe. A new run needs a new approval.
5. Records the result with `report_outcome(approval_id, "succeeded", result=refund,
   target_system="stripe", target_transaction_id=refund["id"])`.
6. Prints `audit.get_decision_receipt(decisionId)`, then requests and downloads the audit
   package to `./audit-package.zip` and prints the `praesidia-verify` command.

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
| `PRAESIDIA_API_KEY` | App: Configure > Integrations > API keys. Scopes `agents:invoke` (decision + outcome) and `audit:read` (receipt + package). The key is shown once. |
| `PRAESIDIA_ORG_ID` | Your organization id (see the SDK docs' Credentials section) |
| `PRAESIDIA_AGENT_ID` | The id of the registered agent that performs the refund (Agents page) |
| `PRAESIDIA_BASE_URL` | Optional. Defaults to `https://api.praesidia.ai` |
| `STRIPE_SECRET_KEY` | Stripe dashboard, test mode: a `sk_test_...` key. Any other key is refused (exit 2) |
| `STRIPE_CHARGE_ID` | A test-mode charge (`ch_...`) of exactly EUR 8,250.00. Once it is fully refunded, Stripe refuses a second refund |

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

Approve the printed approval id in Monitor > Governance > Approvals. Then verify the package
with the offline verifier (`praesidia-verify`, from the audit-verifier project):
`praesidia-verify audit-package.zip`.

This directory is excluded from the published wheel and sdist.
