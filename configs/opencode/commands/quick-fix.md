---
description: Make a small, low-risk fix with lightweight verification
agent: build
---

Implement the requested fix: $ARGUMENTS.

Use the quick-fix workflow:

- Make the smallest safe, localized change; avoid unrelated refactoring.
- Work directly without subagents unless specialist input is genuinely necessary.
- Use the cheapest meaningful verification and report what was verified or intentionally skipped.
- Do not add tests or request formal review when they add no value for the change.

This workflow is ineligible for authentication or authorization, secrets, sensitive data, untrusted input, public API contracts, database schemas or migrations, dependency or lockfile changes, infrastructure or deployment changes, destructive operations, or cross-cutting refactors. If the request affects any of these areas or is not clearly low risk, stop before editing and recommend `/thorough-fix` instead.
