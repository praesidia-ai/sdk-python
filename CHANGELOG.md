# Changelog

## Unreleased

### Fixed
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
