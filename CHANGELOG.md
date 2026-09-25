# Changelog

## Unreleased

### Fixed
- `client.memory.erase()` now matches `POST /organizations/{org}/memories/erase`
  (SDK-0321, BE-1565). The new optional arguments `expected_subject_hash` (64
  lowercase hex, checked before the request) and `acknowledge_cross_org` are sent
  as `expectedSubjectHash` / `acknowledgeCrossOrg` only when set. The method
  returns the 202 pending `DATA_SUBJECT_ERASE` ApprovalRequest; nothing is
  destroyed until a different system admin confirms it. Earlier docs promised
  `memoriesErased` / `dekDestroyed` / `certificateId`; they were wrong. Callers
  that read those keys must read the approval's `status` / `id` instead.
