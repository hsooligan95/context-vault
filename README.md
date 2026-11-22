# ContextVault

**Versioned shared context for multi-agent systems — every entry carries provenance, nothing is silently overwritten, stale-base writes are flagged as conflicts instead of merged, and secret/PII patterns are refused at write time.**

Multi-agent systems usually share state by passing messages and hoping. The fact one agent established, the decision another made, the artifact a third produced — that context deserves a store with rules:

- **Provenance on every version.** Each version records which agent wrote it, when, and the source event it derived from (a run ID, a tool-call ID, a ticket). Provenance is mandatory: a write without an author and a source event is rejected before screening even runs.
- **Nothing is silently overwritten.** An update creates a new version; the old one stays readable forever. Readers follow the head or pin an exact version. Rollback is a *new* version pointing back — history is never rewritten.
- **Stale-base writes are conflicts, not merges.** A writer declares `base_version` — the head it read before writing. If the head has moved, the write is preserved as a *conflicted* version, the head does not move, and `ConflictError` is raised carrying both sides. Resolution is an explicit new write on the real head, never a silent merge.
- **Secrets are refused at the door.** Values are screened in serialized form (so secrets nested in dicts/lists are caught) against a small, high-precision pattern set — AWS access key IDs, GitHub tokens, private-key blocks, bearer tokens, secret-looking assignments, SSNs. A match refuses the write, stores nothing, and the refusal names the pattern classes only — never the matched text.

Local-first: one SQLite file, Python stdlib only, no server, no external services.

## Quick start

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python demo.py        # the four demos below
.venv/bin/python -m pytest      # 21 tests
```

```python
from contextvault import ContextVault

vault = ContextVault("context.db")
vault.write("incident/facts", {"pump": "P-114", "symptom": "cavitation"},
            agent="diagnostics-agent", kind="fact", source_event="run-diag-881")
facts = vault.read("incident/facts")                    # head, with provenance
vault.write("incident/facts", {**facts.value, "inlet_pressure_psi": 41},
            agent="remediation-agent", kind="fact",
            source_event="run-rem-104", base_version=facts.version)
vault.read("incident/facts", version=1)                 # pinned read
vault.history("incident/facts")                         # every version, oldest first
vault.rollback("incident/facts", to_version=1,
               agent="ops-agent", source_event="run-ops-56")
```

Entry kinds are `fact`, `decision`, and `artifact`. Values are any JSON-serializable data.

## Demo 1 — two agents build shared context across a handoff

The remediation agent never receives a message from the diagnostics agent; it picks the work up from the vault, reads the first agent's entries *with their provenance*, and writes on top of them. (Captured from `demo.py`, 2026-11-21.)

```
remediation-agent read: incident/inc-231/facts v1 [fact/current] by diagnostics-agent at 2026-11-21T21:29:16.912084Z (source: run-diag-881, base: None) = {'pump': 'P-114', 'symptom': 'cavitation noise at inlet'}
head: incident/inc-231/artifact v1 [artifact/current] by remediation-agent at 2026-11-21T21:29:16.913270Z (source: run-rem-104, base: None) = {'plan': ['isolate P-114', 'swap inlet filter', 're-test at load']}
head: incident/inc-231/decision v1 [decision/current] by diagnostics-agent at 2026-11-21T21:29:16.912605Z (source: run-diag-881, base: None) = {'action': 'hold production', 'reason': 'inlet pressure dropping'}
head: incident/inc-231/facts v2 [fact/current] by remediation-agent at 2026-11-21T21:29:16.913047Z (source: run-rem-104, base: 1) = {'pump': 'P-114', 'symptom': 'cavitation noise at inlet', 'inlet_pressure_psi': 41}
```

## Demo 2 — full version history, pinned read, rollback

Three writes, three versions, all preserved. The pinned read returns v1 while the head is v3; the rollback creates v4 whose value equals v1 — history grows, it is never edited.

```
  line-3/shift-target v1 [fact/superseded] by planner-agent at 2026-11-21T21:29:16.917557Z (source: run-plan-12, base: None) = 1200
  line-3/shift-target v2 [fact/superseded] by planner-agent at 2026-11-21T21:29:16.924839Z (source: run-plan-13, base: 1) = 1350
  line-3/shift-target v3 [fact/current] by ops-agent at 2026-11-21T21:29:16.925290Z (source: run-ops-55, base: 2) = 900
pinned read (v1, while head is v3): 1200
rollback created: line-3/shift-target v4 [fact/current] by ops-agent at 2026-11-21T21:29:16.981867Z (source: run-ops-56, base: 3) = 1200
head now: 1200 (history still has 4 versions)
```

## Demo 3 — stale-base conflict: flagged, both versions preserved

Both agents read v1 of the maintenance window. The scheduler updates first (head → v2). The ops agent then writes from its stale v1 base — the store refuses to move the head, keeps the write as a flagged `conflicted` v3, and raises:

```
ConflictError: write to 'cell-7/maintenance-window' conflicted: based on v1, but head is v2; stored as conflicted v3, head unchanged
head is still: cell-7/maintenance-window v2 [decision/current] by scheduler-agent at 2026-11-21T21:29:16.997707Z (source: run-sched-31, base: 1) = 'Sat 03:00-05:00'
  cell-7/maintenance-window v1 [decision/superseded] by planner-agent at 2026-11-21T21:29:16.990063Z (source: run-plan-20, base: None) = 'Sat 02:00-04:00'
  cell-7/maintenance-window v2 [decision/current] by scheduler-agent at 2026-11-21T21:29:16.997707Z (source: run-sched-31, base: 1) = 'Sat 03:00-05:00'
  cell-7/maintenance-window v3 [decision/conflicted] by ops-agent at 2026-11-21T21:29:17.003575Z (source: run-ops-77, base: 1) = 'Sun 01:00-02:00'
```

## Demo 4 — a secret is refused at write time

The demo "secret" is `AKIAIOSFODNN7EXAMPLE` — the synthetic example key from AWS's own documentation. The mechanics are real: the write is refused, no key, no version, nothing stored.

```
RedactionError: write to 'deploy/notes' refused: value matches aws_access_key_id pattern(s); nothing was stored
keys in vault after refusal: []
read back: KeyError "no such key: 'deploy/notes'" -- nothing was stored
```

## AgentTrace integration

With a trace ID — `ContextVault(path, trace_id=...)`, or the environment (`CONTEXTVAULT_TRACE_ID`, falling back to `AGENTTRACE_TRACE_ID`) — every operation appends an [AgentTrace](https://github.com/hsooligan95/agent-trace) schema v1 event (`context_write`, `context_conflict`, `context_write_refused`, `context_rollback`) to a JSONL file. Events carry metadata only — key, version numbers, agent, and the value's SHA-256. Values are never emitted, so a refused secret cannot leak through the trace; the refusal event carries only the pattern names. Captured:

```json
{"schema_version": "1", "trace_id": "trace-drill-cv-001", "ts": "2026-11-21T21:29:20.634440Z", "source": "context-vault", "agent": "diagnostics-agent", "type": "context_write", "kind": "event", "payload": {"key": "incident/facts", "version": 1, "kind": "fact", "base_version": null, "value_hash": "f7fbf3ea45c90ed1d72f19af6a5a495ba0bcdc2a83890163e38c58e9fa181f5e"}}
{"schema_version": "1", "trace_id": "trace-drill-cv-001", "ts": "2026-11-21T21:29:20.634713Z", "source": "context-vault", "agent": "ops-agent", "type": "context_conflict", "kind": "event", "payload": {"key": "incident/facts", "version": 2, "base_version": 7, "head_version": 1, "value_hash": "ee900e7bb551801728aa64c65ef160a40abd237e49b5eeee82697552baed71c1"}}
```

These events were validated against agent-trace's own `agenttrace.schema.validate_event` — they ingest into the fleet's trace store unchanged.

## Interlocks

**ContextVault → Factory Floor Agent.** The [factory-floor-agent](https://github.com/hsooligan95/factory-floor-agent)
uses this store as its optional, off-by-default run-summary backend:
one vault key per question, each run writing its summary (question,
plan, groundedness verdict, answer, sources) as a new `artifact`
version with full provenance — agent `factory-floor-agent`, source
event = the run's ID — and the next run on the same question reading
the pinned head version back, provenance included, before it starts.
The integration lives entirely on the consumer side (its
`src/context_vault_backend.py`); this repo is unchanged by it, and
the vault's existing invariants are what make it safe to compose:
provenance-free writes are rejected here, stale-base writes surface
as conflicts here. The consumer's `demo_context_vault.py` shows two
runs — the second in a fresh process — remembering each other through
the vault.

## How it works

One SQLite database, two tables. `versions` holds every version of every key (value as JSON, plus kind, agent, timestamp, source event, base version, status, and the value's SHA-256); `heads` holds each key's current head version. A write is: screen → compare `base_version` to the head → insert a new row (status `current`, or `conflicted` if the base was stale) → advance the head only on a clean write. Conflicted versions get real version numbers, so they are addressable and pinnable like any other version. There is no update-in-place anywhere in the code path, and no delete.

## Limitations

- **Single-process store.** SQLite with default journaling: fine for one host's agent fleet and for demos, not a distributed store. Concurrent writers from multiple processes can serialize on the database lock; there is no cross-host replication or consensus.
- **Conflict detection is per key, at write time.** It catches stale-base writes to the *same* key. It does not detect semantic conflicts across different keys (two keys asserting incompatible facts), and it does not merge anything — resolution is always an explicit new write.
- **Redaction is a guardrail, not DLP.** The pattern set is small and high-precision by design: it will miss novel secret formats, secrets split across fields, and encoded/obfuscated values, and it can false-positive on text that merely resembles an SSN or a token. It stops accidents, not adversaries.
- **Provenance is claimed, not authenticated.** The store records the agent name and source event the writer supplied; it does not verify the writer's identity. Pair with [agent-gate](https://github.com/hsooligan95/agent-gate) if writes need authenticated, policy-checked admission.
- **No retrieval ranking or context assembly.** ContextVault stores and versions context; deciding *which* entries belong in a given prompt's token budget is the caller's job (or a future layer on top).

## Tests

21 tests, all passing: the four demos as executable scenarios, provenance completeness (every version — including conflicted ones — attributable to an agent, a source event, and a timestamp), mandatory-provenance rejection, redaction coverage (7 pattern cases including a secret nested in a list-in-dict, plus "a refusal consumes no version number"), and trace emission (schema v1 shape, propagated ID, no value leakage into the trace, env fallback, silent when unconfigured).
