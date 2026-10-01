# Changelog

All notable changes to `praesidia`. Versions follow SemVer; while on `0.x`, a breaking change
bumps the minor version (see `PUBLISHING.md`, "Semver policy").

## Unreleased

### Changed
- **SDK-2797 (breaking, types only; TS twin in `@praesidia/sdk`):** `agents.create()` is annotated
  `-> AgentCreateResult` (new `TypedDict`, exported from `praesidia`) — be's
  `{agent, clientSecret, credentialMode, webhookSigningSecret}` envelope — instead of
  `dict[str, Any]`. The runtime value is unchanged: be always sent this envelope, so
  `created["id"]` always raised `KeyError`. Read the id from `created["agent"]["id"]`; type
  checkers now flag `created["id"]`. The docstring and README no longer describe a flat agent
  or a `"static"` credential mode (be no longer issues one).

### Added
- **SDK-2504 (needs be BE-1759; TS twin SDK-2503):** interaction hooks send an `Idempotency-Key`
  (UUID v4 per call; a caller key via `decide(..., idempotency_key=)` /
  `report_outcome(..., idempotency_key=)`) on the decision and outcome POSTs and retry a call
  under the same key after a network error, timeout or 429/5xx (never a 409). Each approval
  poll gets a fresh key. New `IdempotencyKeyReusedError` (409 `IDEMPOTENCY_KEY_REUSED`, never
  retried), raised by every client. New `retry=` hooks constructor argument (default policy on;
  `retry=False` restores one attempt). **Behaviour change:** the hooks previously never retried;
  a transient failure now retries within the 15 s budget before the fail mode applies. Additive
  signatures, minor bump under the 0.x policy.
- **SDK-0366 (TS twin SDK-0363; ADR-0004):** `verify_passport` / `verify_ai_system_passport` /
  `fetch_and_verify*` read `proof.signatureFormat` (absent = 1). Format 2 verifies over
  `b"praesidia:trust-passport:v2\n" + canonical JSON`; format 1 passports verify unchanged. A
  signature made for another purpose (e.g. `governance-badge`) does not verify. **Behaviour change:**
  a `signatureFormat` other than absent or the JSON integer `1`/`2` (`None`, `"2"`, `3`, `True`,
  `2.0`) is `malformed-passport`. Minor bump under the 0.x policy.
- **SDK-0362 (needs be BE-1808; TS twin SDK-0361):** `report_outcome(status=..., decision_id=...)`
  reports the outcome of a plain `allow` (`approvalId` `None`) by its `decisionId`. Pass exactly one
  of `approval_id` / `decision_id`; neither or both raises a `PraesidiaConfigError` that is also a
  `ValueError`, before any request. Existing `report_outcome(approval_id, status)` calls are
  unchanged. `InteractionOutcomeReceipt.approvalId` is now `Optional[str]` (typing only: `None` on
  the decision path) and gains `reportedDecisionId`. A second report is 409; another principal 403.

### Changed
- **Breaking (SDK-2511):** `requires-python` is now `>=3.11` (was `>=3.9`; 3.9 is EOL, 3.10 is
  EOL 2026-10-31). Base dependency floor `httpx>=0.28.1,<1`. Framework extras pinned to
  `google-adk==2.10.0`, `agent-framework-core==1.19.0`, `agno==3.0.11`, `langgraph==1.2.12`
  (CrewAI test group `1.15.23`); `openai-agents` stays `0.20.0` because CrewAI still requires
  `openai<3`. Wheels now carry Core Metadata 2.5 (hatchling 1.32.4). Needs a 0.x minor bump.

### Fixed
- **SDK-2511:** `memory.list(source_type="IMPORT")` raised `NameError` (a `create()`-only
  `access_source_id` check had been copied into `list()`); it now sends the filter.
- Public type hints: return/parameter annotations written `list[...]` inside classes with a
  `list()` method resolved to the method under type checkers; they now use `builtins.list`.
- **Behaviour change (SDK-0359, security; TS twin SDK-0358):** a request header httpx would refuse
  (a `chain_id` or caller header containing CR, LF, NUL, another control character, or any
  non-ASCII character; a header name that is not an RFC 9110 token; a non-`str` value) now raises
  `PraesidiaConfigError` before any request is built, naming the header but never its value. Before,
  httpx's `LocalProtocolError` read as a transport outage and `Guard` degraded to local rules in
  `local_rules`/`fail_open`. Config errors are never retried. Non-UUID ASCII chain ids stay legal.
  Unlike the TS SDK, obs-text (0x80-0xff) is refused: httpx encodes `str` header values as ASCII.

## 0.5.0 — release candidate, not yet published (no `v0.5.0` tag; not on PyPI)

No earlier version reached PyPI (0.2.x to 0.4.x existed only in source), so 0.5.0 carries every
change below. It is the first version intended for the PyPI registry.

### INTEG-0110: release packaging

- **Removed** the `crewai` extra, and CrewAI from the `frameworks` extra. CrewAI 1.15.20 resolves
  ChromaDB 1.1.1, which has four open advisories with no fixed version
  (`docs/runtime-integrations.md`). The adapter module still ships and is still tested in CI
  through the unpublished `crewai` dependency group; install `crewai==1.15.20` yourself to use it.
- The publish workflow uses PyPI trusted publishing (OIDC) from a `pypi` environment and uploads
  PEP 740 attestations. No PyPI token is stored in the repository.
- The wheel ships only `praesidia/` plus `dist-info` (metadata, `LICENSE`). A stray tracked
  `.venv` symlink is removed from the repository so it cannot reach the sdist.
- Added classifiers (`Operating System :: OS Independent`, `Topic :: Security`,
  `Topic :: Software Development :: Libraries :: Python Modules`) and a `Changelog` project URL.
  The `Documentation` URL points at this repository's README: `docs.praesidia.ai` does not
  resolve yet, and a release's metadata cannot be changed after upload.
- The changelog moved out of `README.md` into this file.

### Fixed
- **Behaviour change (SDK-0355, security):** `Guard` no longer degrades to local rules when the
  request body cannot be encoded. A lone UTF-16 surrogate in content (which `json.loads` keeps)
  or a NaN/Infinity in `context` made httpx raise before sending, and every `failure_mode` except
  `fail_closed` then returned the local-rules verdict with `degraded: True`, skipping the org's
  guardrails. The degrade predicate is now an allowlist (httpx transport/timeout error, 408, 5xx,
  malformed 2xx); any other error raises a `PraesidiaError` (`__cause__` = the original error) in
  every mode, from `check_input`/`check_output`/`run`/`protect` and `log_task`. Content is not
  sanitised. Interaction hooks already rejected these arguments with `PraesidiaConfigError`.
- **Behaviour change (SDK-0353, security, parity with TS SDK-0352):** interaction hooks no
  longer treat a 429 as an outage. A fail-open hook used to return
  `InteractionHookResult(decision=None)` on a rate limit an end user can trigger from a shared
  egress IP, and a fail-closed one raised `InteractionDecisionUnavailableError`; both now raise
  `RateLimitError`, and a 429 while waiting for an approval raises instead of re-polling. Hooks
  and `Guard` now share one degrade predicate (transport error, timeout, 408, 5xx, malformed 2xx).
- **Behaviour change (SDK-0351, security):** `fetch_and_verify(agent_id)` and
  `fetch_and_verify_ai_system(ai_system_id)` now bind the passport to the requested id.
  Previously a genuine same-org passport for a different agent verified, even under
  pinned keys. A `credentialSubject.id` other than `did:web:praesidia.ai:agents:<agent_id>` /
  `did:web:praesidia.ai:ai-systems:<ai_system_id>` (case-insensitive) now returns
  `verified: False, signatureValid: True, reason: "subject_mismatch"`. `verify_passport` and
  `verify_ai_system_passport` (module functions and `client.trust` methods) take a new optional
  `expected_subject` that applies the same check. TS parity: `@praesidia/sdk` SDK-0350.
- **Behaviour change (SDK-0349, security):** `Guard` no longer degrades to local rules on a
  caller-triggerable 4xx. Only an outage (transport error, timeout, 408, 5xx, malformed 2xx)
  follows `failure_mode`; any other 4xx including 429 now raises in `local_rules` and `fail_open`
  too, from `check_input`/`check_output`/`guard_*`/`run`/`protect` and `log_task`. Previously an
  end user could send >100 000 characters (400) or trip the 20/min throttle (429) and have the
  org's guardrails silently skipped. New: `MAX_GUARD_CONTENT_LENGTH` and
  `GuardContentTooLargeError`, raised locally before sending oversized content.
- **Behaviour change (SDK-0339):** an `http:` `base_url` / `PRAESIDIA_BASE_URL` to a non-loopback
  host now raises `PraesidiaConfigError` instead of sending the API key in cleartext. Loopback
  (`localhost`, `127.0.0.0/8`, `::1`) is unaffected. Opt back in with the new keyword
  `allow_insecure_http=True` on `Praesidia`, `Guard`, `PraesidiaTrust` and the interaction hooks,
  or `PRAESIDIA_ALLOW_INSECURE_HTTP=1`. Other malformed base URLs still raise `ValueError`.
- `client.ai_systems.transition_lifecycle()` to `production` or `retired` always got a 400 since
  be AISYS-0018 made those targets approval-gated (SDK-0323). It now raises
  `PraesidiaConfigError` (a `PraesidiaError`) before sending, naming the method to use. New
  methods reach the approval routes: `request_lifecycle_transition(ai_system_id, to_status, *,
  reason=None)`, `approve_lifecycle_transition(request_id, *, reason=None)`,
  `reject_lifecycle_transition(request_id, *, reason=None)`, `retire(ai_system_id, *,
  retention_policy, reason, retention_until=None)` (202) and `reapprove(ai_system_id,
  material_change_id, *, reason=None)`. Callers that caught the 400 as `PraesidiaError` still
  catch it.
- `client.memory.erase()` now matches `POST /organizations/{org}/memories/erase`
  (SDK-0321, BE-1565). The new optional arguments `expected_subject_hash` (64
  lowercase hex, checked before the request) and `acknowledge_cross_org` are sent
  as `expectedSubjectHash` / `acknowledgeCrossOrg` only when set. The method
  returns the 202 pending `DATA_SUBJECT_ERASE` ApprovalRequest; nothing is
  destroyed until a different system admin confirms it. Earlier docs promised
  `memoriesErased` / `dekDestroyed` / `certificateId`; they were wrong. Callers
  that read those keys must read the approval's `status` / `id` instead.

### SDK-0327: Decision Receipts + audit packages (BE-1581, BE-1629)

- **Added** `audit.get_decision_receipt(decision_id)`, `audit.get_receipt(row_id)`,
  `audit.request_package(from_date=, to_date=, ai_system_id=)`, `audit.get_package(id)` and
  `audit.download_package(id) -> bytes` (bounded 128 MiB transport).
- **Added** `export_bundle(include_unrooted=False)` and the `AuditBundle` return type carrying
  `requested_to` / `effective_to` / `window_clamp` from the response headers. `AuditBundle`
  subclasses `bytes`, so existing callers are unaffected. **Semver minor**, no breaking change.
  TS↔Python parity: mirrors `sdk`'s SDK-0326.

### SDK-0318: asset writes send only client sources (BE-1529)

- **Fixed** `create_asset` / `put_asset_by_external_id` now raise `ValueError` on a `source`
  outside the new `AiSystemsResource.CLIENT_ASSET_SOURCES` (`manual`/`api`/`import`), before
  sending. Previously `discovery_connector`/`runtime_observation`/`entitlement_projection`
  reached be and got a 400. `create_asset` now also validates `assetType`/`discoveryStatus`,
  as `put_asset_by_external_id` already did.
- **Fixed** `ASSET_SOURCES` (5 → 6: adds `entitlement_projection`). Before this,
  `list_assets*(source="entitlement_projection")` raised `ValueError` even though be's list
  filter accepts it.
- **Behaviour change (semver patch)**: an invalid create/put value now raises `ValueError`
  instead of the server's 400 `PraesidiaError`. Any value that now raises was already
  rejected by be ≥ BE-1529. TS↔Python parity: mirrors `sdk`'s SDK-0317.

### SDK-0301: interaction hooks, an advisory in-runtime guard (BE-1486)

- **Added** `PraesidiaInteractionHooks` (httpx.Client) and `AsyncPraesidiaInteractionHooks`
  (httpx.AsyncClient): `before_tool_call`, `before_exec`, `before_fs_access`,
  `before_browser_action`, `before_interaction`, `decide`, `guarded`, plus `INTERACTION_TYPES`,
  `INTERACTION_VERDICTS`, `DEFAULT_FAIL_MODES`, `InteractionHookResult`, `InteractionDecision`
  (`praesidia/interaction_hooks.py`) and `InteractionDeniedError` /
  `InteractionDecisionUnavailableError` (`praesidia/exceptions.py`). Calls be's
  `POST /organizations/{orgId}/interaction-decisions`; replays be's recorded fixture
  `test-fixtures/interaction-decision-v1.json` (the same file the TS SDK replays). TS↔Python
  parity: mirrors `sdk`'s SDK-0300 (same defaults, verdicts, fail modes, cache and approval wait);
  `guarded` is Python-only. Additive only, no breaking change (semver minor).

### SDK-0315: `ASSET_TYPES` adds `GUARDRAIL`

- **Fixed** `AiSystemsResource.ASSET_TYPES` (23 → 24: adds `GUARDRAIL`) to match
  `ui/swagger.json`'s `AiAsset.assetType` enum (be BE-0338). Before this, `list_assets*`,
  `put_asset_by_external_id` and `traverse` raised `ValueError` on `"GUARDRAIL"` before sending
  the request. `RELATIONSHIP_TYPES` re-checked against the same swagger: already
  in sync (13 values). TS↔Python parity: mirrors `sdk`'s SDK-0314. No breaking changes — widened
  valid-value set only.

### SDK-0311: `PraesidiaTrust`, the trust routes without credentials

- **Added** `PraesidiaTrust(base_url=None, *, timeout=30.0, retry=None)`
  (`praesidia/trust.py`, exported from `praesidia`): a `TrustResource` that
  needs no `api_key` / `org_id`, so a verifier with no Praesidia account can call
  every trust fetch (`fetch_ai_system_passport_pdf`, `fetch_and_verify_ai_system`,
  …) without placeholder credentials. `base_url` falls back to `PRAESIDIA_BASE_URL`,
  then `https://api.praesidia.ai`. TS parity: `new PraesidiaTrust()`. No breaking
  changes — `Praesidia(...)` and `client.trust` are unchanged.

### SDK-0310: public AI System passport routes (BE-0540)

- **Added** to `TrustResource` (`praesidia/trust.py`): `fetch_ai_system_passport`,
  `fetch_ai_system_verify_bundle` (both `dict`, shaped like be's
  `ai-system-trust-passport.dto.ts`) and `fetch_ai_system_badge_svg` (`str`) for
  `GET /trust/passport/ai-systems/{ai_system_id}[/verify|/badge.svg]` — public,
  sent without the API key, like `fetch_ai_system_passport_pdf`. TS parity:
  `PraesidiaTrust.fetchAiSystemPassport` / `fetchAiSystemVerifyBundle` /
  `fetchAiSystemBadgeSvg`. No breaking changes — additive only.

### SDK-0302: `by-external-id` desired-state methods (PRAE-228/229)

- **Added** to `AiSystemsResource` (`praesidia/ai_systems.py`): `put_system_by_external_id`/
  `delete_system_by_external_id`, `put_asset_by_external_id`/`delete_asset_by_external_id`,
  `put_relationship_by_external_id`/`delete_relationship_by_external_id` (be's BE-0579
  desired-state API) — the shape IaC tooling (Terraform provider PRAE-228, k8s operator
  PRAE-229) needs. Each returns `{"id", "externalId", "created", "changed", "updatedAt",
  "resource"}` (`DesiredStateOutcomeDto`); `changed` is the plan-stability signal, surfaced not
  swallowed. New `HttpClient.put`/`.delete_returning` (`_http.py`) back them — `put` is retried
  like `get`/`delete` (PUT is naturally idempotent, no `idempotency_key` needed) and
  `delete_returning` is `delete`'s sibling for a DELETE route that answers with a JSON body
  instead of 204. TS↔Python parity: mirrors `sdk`'s SDK-0302. No breaking changes — additive
  only.

### SDK-0304: `RELATIONSHIP_TYPES` adds `WRITES`

- **Fixed** `AiSystemsResource.RELATIONSHIP_TYPES` (12 → 13: adds `WRITES`) to match
  `ui/swagger.json`'s `AssetRelationship.relationshipType` enum (DB-0502, the write half of the
  PRAE-161 lineage chain). No breaking changes — widened valid-value set only.

### SDK-0007: `ASSET_TYPES`/`RELATIONSHIP_TYPES` contract sync

- **Fixed** `AiSystemsResource.ASSET_TYPES` (20 → 23: adds `TOOL`, `API_ENDPOINT`, `DATA_SCOPE`)
  and `.RELATIONSHIP_TYPES` (9 → 12: adds `CAN_INVOKE`, `GRANTS_SCOPE`, `CAN_ASSUME`) to match
  `ui/swagger.json`'s `AiAsset.assetType`/`AssetRelationship.relationshipType` enums (DB-0300).
  `traverse`'s `asset_types`/`relationship_types` filters are now client-side validated against
  the synced tuples (same `ValueError` shape as `direction`) — previously skipped because the
  constants lagged be's enum (SDK-0006). New test
  `test_asset_and_relationship_types_match_openapi` reads the sibling `ui/swagger.json` and fails
  if the tuples drift again. No breaking changes to signatures — widened valid-value sets and a
  new (additive) client-side check that only rejects values be already 400s on.

### SDK-0006: `traverse` (AISYS-0003) + `summary` (AISYS-0004)

- **Added** `AiSystemsResource.traverse(asset_id, **filters)`
  (`GET .../asset-relationships/graph/traverse`) and `.summary(ai_system_id)`
  (`GET .../ai-systems/:id/summary`, added opportunistically — same contract batch, cheap to
  cover in the same item) once both routes landed on `ui/swagger.json`. `traverse`'s `direction`
  is client-side validated (`"downstream"`/`"upstream"`/`"both"`); `asset_types`/
  `relationship_types` are not (see the note above the method table). No breaking changes —
  additive methods only, no existing signature touched.

### SDK-0004: full CONTRACT parity for the AI System / asset / relationship graph

- **Added** to `AiSystemsResource` (`praesidia/ai_systems.py`): AI System `update_owners`,
  `transition_lifecycle`, `delete`; AI Asset `create_asset`/`get_asset`/`update_asset`/
  `archive_asset`/`restore_asset`; membership `change_asset_role`; relationship
  `get_relationship`/`update_relationship`/`archive_relationship`/`restore_relationship`. Closes
  the SDK-0002 exclusions except multi-hop graph `traverse` (AISYS-0003 — landed in SDK-0006 once
  the route reached `ui/swagger.json`). See
  [AI Systems / assets / relationship graph](README.md#ai-systems--assets--relationship-graph-sdk-0002sdk-0004-parity-with-bes-aisys-0002-and-sdks-sdk-0001sdk-0003)
  for the full method table. No breaking changes — additive methods only, no existing signature
  touched.

### SDK-0002: AI System / asset / relationship graph resource

- **Added** `AiSystemsResource` (`praesidia/ai_systems.py`), exposed as `client.ai_systems`,
  parity with `sdk`'s (TypeScript) `PraesidiaAiSystems` (SDK-0001) and be-core's AISYS-0002
  module: AI System CRUD (`list`/`get`/`create`/`update`/`archive`/`restore`), AI Asset
  read/adopt (`list_assets`/`adopt_asset`), AI System ↔ Asset membership
  (`attach_asset`/`detach_asset`), and asset relationship create/list
  (`create_relationship`/`list_relationships`). Every list method follows the SCAN2-011
  `list`/`*_page`/`*_all` convention. See
  [AI Systems / assets / relationship graph](README.md#ai-systems--assets--relationship-graph-sdk-0002-parity-with-bes-aisys-0002-and-sdks-sdk-0001)
  for the exact method table and documented exclusions (unchanged from SDK-0001). No breaking
  changes — new resource only, no existing export or signature touched.

### TOP-0008: `Guard` convenience wrapper + offline local-rules guardrail fallback

- **Added** `praesidia.Guard` (`praesidia/guard.py`) and
  `praesidia.local_rules.run_local_rules` (`praesidia/local_rules.py`), the Python port of `sdk`'s
  `PraesidiaGuard`/`runLocalRules` (`sdk/src/guard.ts`, `sdk/src/local-rules.ts`). Closes the gap
  where Python integrators had `protected_http`/`integrations.protected_tool` but no convenience
  wrapper and no offline guardrail fallback — see [Guard](README.md#guard--guardrail-checks--audit-logging-with-an-offline-fallback-top-0008)
  above for the full surface, including the Python-idiomatic `@guard.protect(...)` decorator and
  `TaskHandle` context-manager addition over the TS callback-closure shape.
- Local-rule patterns are compiled with `re.ASCII` — JS's `\d`/`\w`/`\b` are always ASCII-only
  regardless of flags, while Python's `re` module defaults to Unicode-aware; without `re.ASCII` a
  non-ASCII digit run (e.g. Eastern Arabic-Indic digits) would trip the SSN/credit-card patterns
  in Python but never in the TS SDK. See `tests/test_local_rules.py`'s parity table, verified
  against a live run of `sdk`'s compiled `runLocalRules`.
- Purely additive — no existing method signature changed.

### AUD-0063: close the analytics resource coverage gap

- **Added** 11 `AnalyticsResource` methods closing be-core's remaining
  `/organizations/{org_id}/analytics*` routes: `capture_state`,
  `agent_analytics`, `events`, `activity_log`, `record_event`,
  `security_metrics`, `usage_heatmap`, `compliance_metrics`, `anomalies`,
  `cost_by_team`, `model_comparison`. `AnalyticsResource` previously covered
  5 of be-core's 15 analytics paths; it now covers all of them. Purely
  additive — no existing method signature changed.
- **Added** a swagger.json-derived coverage test
  (`tests/test_analytics_coverage.py`, mirrored in the TypeScript SDK) that
  fails on any `/organizations/{org_id}/analytics*` operation this resource
  does not implement, so a future be-added route is caught here instead of
  silently missing the SDK.
- `record_event` is a bare, never-retried POST (not in be-core's
  `Idempotency-Key` allowlist) and requires `ANALYTICS_CREATE` — no mintable
  API-key scope exists for it, so it needs a JWT bearer. Every other new
  method is an idempotent GET, retried per the existing policy.

### production contract hardening

- **Fixed** compliance report generation validating its polling options only
  after enqueueing a report; invalid options now fail before any network side
  effect.
- **Fixed** agent-memory enum drift against the backend and fail fast on
  invalid content, search limits, and retention settings that the backend
  would reject or silently ignore. Retention days now require the `CUSTOM`
  regime.
- **Hardened** retry, workflow-budget, chain-header, analytics, audit-export,
  and OTLP telemetry validation against non-finite, unsafe, or backend-invalid
  values.
- **Fixed** isolated release builds producing Core Metadata 2.5 artifacts that
  Twine 6.2 cannot validate; the backend and release tools are pinned and the
  wheel/sdist now emit publishable Metadata-Version 2.4.
- **Expanded** behavioral coverage across every public resource method,
  authentication mode, legacy response envelope, and typed HTTP error mapping.

### PA-0026: fix `protect_action`'s deny discriminator (defect in PA01 DX-002)

- **Fixed** `protect_action` misclassifying a downstream tool/transport error as a pre-dispatch
  policy denial. The shipped heuristic (`errorCode != 'TOOL_ERROR'`) was broken: `'TOOL_ERROR'` is
  never present in this endpoint's caller-visible response, so both a real tool exception
  (`errorCode: 'BAD_REQUEST' | 'INTERNAL_ERROR'`) and a successful call whose tool errored (no
  `errorCode` at all) satisfied the old "raise" condition. Switched the discriminator to presence
  of the response's `actionDenyReason` field, which `be` sets on and only on genuine pre-dispatch
  denials.
- **Added** `ProtectedActionDeniedError.action_deny_reason` (one of `"PERMIT_MISSING"` |
  `"PERMIT_INVALID"` | `"PERMIT_EXPIRED"` | `"PERMIT_MISMATCH"` | `"PERMIT_REPLAYED"` |
  `"POLICY_DENIED"`).
- No breaking change to `protect_action`'s return shape; `ProtectedActionDeniedError` gained an
  additive attribute.

### PA01 DX-002: `AgentsResource.protect_action` (blocking/raising Proof Edge wrapper)

- **Added** `client.agents.protect_action(...)` — a blocking, raising wrapper over the managed MCP
  Proof Edge (`POST /organizations/{org_id}/mcp-servers/{server_id}/tools/{tool_name}/call`).
  Raises `ProtectedActionDeniedError` on a pre-dispatch denial (missing/expired/invalid/mismatched
  Permit, or a confirmed replay) and `UnsupportedProtectedActionTargetError` for any `protocol`
  other than `"mcp"` — never silently downgrades to `call_mcp_tool`-style unraised behaviour.
- **Added** `jcs_canonicalize`/`jcs_commitment`/`JcsCanonicalizationError` (RFC 8785 JCS) —
  byte-compared against the shared `sdk`/`be`/`audit-verifier` golden fixtures.

## 0.4.1 — organization runtime installation binding (unpublished)

Adds explicit `runtime_installation_id` and `PRAESIDIA_RUNTIME_INSTALLATION_ID`
binding to protected HTTP checkpoints and managed-tool durable state. Conflicting
installation IDs fail before network access. The backend can now disable future
bound execution while preserving authenticated checkpoint readback.

## 0.4.0 — managed protected runtime tools (unpublished)

- Added reusable exact-request managed tools and six optional native framework
  adapters; native state is a recovery cursor, never approval authority.
- Added a separately installable Hermes plugin with native durable state,
  session/call binding and explicit default blocking of unrelated tools.
- Preserved pending/denied/expired/partial/unknown distinctions and once-only
  resume handling; no native effect callable or approval Boolean is accepted.
- Added actual framework tool, native approval serialization, and separate
  process restart tests plus a CLI for full-backend acceptance without model keys.
- Base dependencies and Python 3.9+ management compatibility remain unchanged;
  exact supported optional runtime versions are documented separately.

## 0.3.1 — R-SDK-1: allow-list the routes `idempotency_key` may retry

- **Fixed** `idempotency_key` retry is now allow-listed to the routes
  be-core actually deduplicates (`POST /organizations/:orgId/tasks`,
  `POST /a2a/tasks`, `POST /a2a/tasks/:taskId/result`); every other path
  raises `ValueError` instead of retrying a write the server can
  double-apply. Previously any path accepted the option. **Behavioral,
  non-breaking for existing callers** — no shipped resource method passed
  `idempotency_key` before this fix, so no caller's request shape changes;
  the transport-level escape hatch is simply narrower/safer than before.

## 0.3.0 — bounded retry (FINDING-4) + dev environment

- **Added** bounded, idempotency-safe retry to `HttpClient` (`get`/`delete`
  retry by default; `post`/`patch` only retry when called with
  `idempotency_key=...`). New `retry` constructor kwarg on `Praesidia`
  (`RetryConfig` instance, `None` for the default policy, or `False` to
  disable). Non-breaking — no existing method signature changed.
- **Dev environment**: this repo's `uv.lock` is the source of truth for a
  reproducible dev environment — `uv sync --extra dev && uv run pytest -q`
  runs the full suite without touching the ambient system Python. (`pip
  install -e ".[dev]"` remains a valid alternative for contributors without
  `uv`.)

## 0.2.0 — audit / download bug-fix wave

- **BREAKING — `audit.list()` drops the `resource_type` argument.** The
  backend `FilterAuditDto` never accepted a `resourceType` query param and
  rejects unknown params (`forbidNonWhitelisted`), so *every* call that
  passed `resource_type` got a hard `400` for the whole request — it never
  worked, so no functioning caller can break. `resourceType` is a value the
  backend *derives* from the `action` prefix at read time, not a stored,
  queryable column. **Filter by `action` instead** (e.g.
  `client.audit.list(action="agent.created")`). *(BUGHUNT-SDK-03)*
- **`audit.stream(limit=...)` now streams the full range for any `limit`.**
  The backend hard-caps a page at 100 rows (`PAGINATION_MAX_LIMIT`); the old
  "stop when a page is shorter than `limit`" heuristic treated the first
  clamped page as the last and silently dropped every row past the first 100
  whenever `limit > 100`. The iterator now advances until the server returns
  an empty page, so a `stream(limit=500)` over 5,000 rows yields all 5,000.
  *(BUGHUNT-SDK-01)*
- **Bulk downloads no longer hang forever on a stalled server.**
  `compliance.get_pdf`, `audit.export` and `analytics.export` used
  `timeout=None`, which disabled *all* httpx timeouts (connect/read/write/
  pool). They now use a finite per-operation budget (`connect=10s`,
  `read=60s` idle-between-chunks, `write=30s`, `pool=10s`) that still lets a
  large-but-progressing download finish, plus an optional `timeout=` override.
  *(BUGHUNT-SDK-06)*
- **Offline passport canonical-JSON matches be-core for astral object keys.**
  `praesidia._crypto.canonical_json` now sorts object keys by their UTF-16
  code-unit sequence (matching V8 / `Array.prototype.sort`), so a passport
  with dynamic non-BMP keys reconstructs the same signing preimage be-core
  signed instead of falsely failing verification. *(BUGHUNT-SDK-04)*
