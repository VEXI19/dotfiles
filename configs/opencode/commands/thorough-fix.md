---
description: Implement a complex or high-risk change with rigorous verification
agent: build
---

Implement the requested change: $ARGUMENTS.

Use the thorough-fix workflow:

1. Inspect the codebase and establish the scope before editing.
2. Consult only the domain specialists relevant to the change.
3. Implement the change in focused, maintainable steps.
4. Run appropriate tests, builds, linting, type checks, or other focused verification.
5. Ask `test-expert` to assess the verification coverage.
6. Ask `code-reviewer` to review the completed change and address material findings.
7. Consult `security-auditor` when the change affects authentication, authorization, secrets, sensitive data, untrusted input, or other security-sensitive behavior.
8. Summarize the specialists consulted, verification performed, and any remaining limitations.
