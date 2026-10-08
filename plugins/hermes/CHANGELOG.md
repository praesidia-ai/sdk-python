# Changelog

All notable changes to `praesidia-hermes`. Versions follow SemVer; while on `0.x`, a breaking change
bumps the minor version. This package is versioned separately from `praesidia` (the SDK).

## 0.1.1 — 2026-10-08 (first PyPI release)

No earlier version reached PyPI: 0.1.1 existed only in source until now, so this entry
covers everything the package does.

- Registers the `praesidia` entry point in the `hermes_agent.plugins` group: the managed
  `praesidia_protected_http` tool, its pre-tool hook and execution middleware, and a strict tool
  boundary that is on by default (`strict_tools: true`).
- Tested against Nous Hermes Agent at upstream commit
  `0390ace8179f4cf75bd3941e590dd74e638672b6` on Python 3.11.
- Depends on `praesidia>=0.5.0,<0.6`: any 0.5.x SDK patch, but not the next breaking minor. The
  earlier source-only pin `praesidia==0.5.0` would have blocked every SDK patch release.
- Package metadata: MIT license, classifiers, and project links (repository, issues, this
  changelog).
