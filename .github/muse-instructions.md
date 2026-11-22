# Copilot instructions — context-vault

You are working in **ContextVault**, versioned shared context for
multi-agent systems: agents read and write entries (facts, decisions,
artifacts) where every version carries provenance, nothing is silently
overwritten, stale-base writes are flagged as conflicts instead of
merged, and secret/PII patterns are refused at write time.

## Stack

- Python 3, standard library only (SQLite-backed store), zero runtime
  dependencies.
- `src/contextvault/`: `store.py` (versions + heads tables),
  `redaction.py` (write-time screening patterns), `trace_emit.py`
  (AgentTrace schema v1 emission).

## Tests

```bash
.venv/bin/python -m pytest   # 21 tests
python demo.py               # handoff, history/rollback, conflict, redaction demos
```

## Architecture notes

- Nothing is updated in place or deleted: an update is a new version,
  and rollback is a new version pointing back. History is never
  rewritten.
- Provenance (agent, timestamp, source event) is mandatory — a write
  without it is rejected before screening even runs.
- A writer declares `base_version`; if the head has moved, the write is
  preserved as a `conflicted` version, the head does not move, and
  `ConflictError` is raised carrying both sides. Resolution is an
  explicit new write on the real head — never a silent merge.
- Values are screened in serialized form (secrets nested in dicts/lists
  are caught). Refusals name pattern classes only, never matched text.
- Trace emission carries metadata and value SHA-256 hashes only — never
  values. Emission env: `CONTEXTVAULT_TRACE_ID`, falling back to
  `AGENTTRACE_TRACE_ID`; silent when neither is set.

## Shared rules

- Use Unix LF line endings in every file; never CRLF.
- Never commit secrets, tokens, or credentials — real or synthetic-looking.
- Tests must pass before you describe a change as complete; run the test
  command above and report honestly if you cannot.
- Prefer the smallest complete implementation: reuse what already exists in
  this repo before adding new code, files, or dependencies.
