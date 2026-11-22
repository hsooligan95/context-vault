---
mode: agent
description: Review the current diff against context-vault's invariants
---

Review my current diff against this repo's invariants:

- Nothing is updated in place or deleted: an update is a new version, and rollback is a new version pointing back.
- Provenance (agent, timestamp, source event) is mandatory — a write without it is rejected before screening even runs.
- A stale-base write is preserved as a conflicted version, the head does not move, and ConflictError is raised — never a silent merge.
- Trace emission carries metadata and value SHA-256 hashes only, never values; refusals name pattern classes, never matched text.

Flag every violation with file and line. Also flag any change that
alters a documented behavior contract (output shapes, exit codes,
event schemas, recorded artifacts) without updating the docs and
tests that describe it. No style nitpicks — correctness and contract
violations only.
