---
mode: agent
description: Write tests for a named module in context-vault
---

Write tests for the module I name, following this repo's existing test
style, layout, and fixtures. Cover the behavior the module documents,
including these invariants where they apply:

- Nothing is updated in place or deleted: an update is a new version, and rollback is a new version pointing back.
- Provenance (agent, timestamp, source event) is mandatory — a write without it is rejected before screening even runs.
- A stale-base write is preserved as a conflicted version, the head does not move, and ConflictError is raised — never a silent merge.

Then run the test suite:

    .venv/bin/python -m pytest

Show the result. Do not claim done unless the suite is green; if a
test cannot pass, say why instead of weakening the assertion.
