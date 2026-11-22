"""ContextVault demos -- the four scenarios the README shows.

Run:  python demo.py
Each demo uses its own throwaway SQLite file and prints exactly what
the store did. Demo 4's "secret" is a synthetic, obviously fake value
invented for this script; the mechanics are real.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from contextvault import ConflictError, ContextVault, RedactionError


def fresh_vault() -> ContextVault:
    path = Path(tempfile.mkdtemp()) / "demo.db"
    return ContextVault(path)


def demo_handoff() -> None:
    print("=== Demo 1: two agents build shared context across a handoff ===")
    vault = fresh_vault()
    # Agent A (diagnostics) establishes what it found.
    vault.write("incident/inc-231/facts",
                {"pump": "P-114", "symptom": "cavitation noise at inlet"},
                agent="diagnostics-agent", kind="fact",
                source_event="run-diag-881")
    vault.write("incident/inc-231/decision",
                {"action": "hold production", "reason": "inlet pressure dropping"},
                agent="diagnostics-agent", kind="decision",
                source_event="run-diag-881")
    # Agent B (remediation) picks the work up from the vault, not from a
    # message thread: it reads A's entries and writes on top of them.
    facts = vault.read("incident/inc-231/facts")
    print(f"remediation-agent read: {facts}")
    vault.write("incident/inc-231/facts",
                {"pump": "P-114", "symptom": "cavitation noise at inlet",
                 "inlet_pressure_psi": 41},
                agent="remediation-agent", kind="fact",
                source_event="run-rem-104", base_version=facts.version)
    vault.write("incident/inc-231/artifact",
                {"plan": ["isolate P-114", "swap inlet filter", "re-test at load"]},
                agent="remediation-agent", kind="artifact",
                source_event="run-rem-104")
    for key in vault.keys():
        head = vault.read(key)
        print(f"head: {head}")
    vault.close()


def demo_history_rollback() -> None:
    print("\n=== Demo 2: full version history, pinned read, rollback ===")
    vault = fresh_vault()
    vault.write("line-3/shift-target", 1200, agent="planner-agent",
                kind="fact", source_event="run-plan-12")
    vault.write("line-3/shift-target", 1350, agent="planner-agent",
                kind="fact", source_event="run-plan-13", base_version=1)
    vault.write("line-3/shift-target", 900, agent="ops-agent",
                kind="fact", source_event="run-ops-55", base_version=2)
    for entry in vault.history("line-3/shift-target"):
        print(f"  {entry}")
    pinned = vault.read("line-3/shift-target", version=1)
    print(f"pinned read (v1, while head is v3): {pinned.value}")
    rolled = vault.rollback("line-3/shift-target", to_version=1,
                            agent="ops-agent", source_event="run-ops-56")
    print(f"rollback created: {rolled}")
    print(f"head now: {vault.read('line-3/shift-target').value} "
          f"(history still has {len(vault.history('line-3/shift-target'))} versions)")
    vault.close()


def demo_conflict() -> None:
    print("\n=== Demo 3: stale-base write is flagged, both versions preserved ===")
    vault = fresh_vault()
    vault.write("cell-7/maintenance-window", "Sat 02:00-04:00",
                agent="planner-agent", kind="decision", source_event="run-plan-20")
    # Both agents read v1. Agent A updates first -- head moves to v2.
    vault.write("cell-7/maintenance-window", "Sat 03:00-05:00",
                agent="scheduler-agent", kind="decision",
                source_event="run-sched-31", base_version=1)
    # Agent B writes from its stale copy of v1.
    try:
        vault.write("cell-7/maintenance-window", "Sun 01:00-02:00",
                    agent="ops-agent", kind="decision",
                    source_event="run-ops-77", base_version=1)
    except ConflictError as exc:
        print(f"ConflictError: {exc}")
    print(f"head is still: {vault.read('cell-7/maintenance-window')}")
    for entry in vault.history("cell-7/maintenance-window"):
        print(f"  {entry}")
    vault.close()


def demo_redaction() -> None:
    print("\n=== Demo 4: a write carrying a secret is refused at the door ===")
    vault = fresh_vault()
    try:
        vault.write("deploy/notes",
                    {"target": "prod", "aws_access_key_id": "AKIAIOSFODNN7EXAMPLE"},
                    agent="deploy-agent", kind="fact", source_event="run-dep-9")
    except RedactionError as exc:
        print(f"RedactionError: {exc}")
    print(f"keys in vault after refusal: {vault.keys()}")
    try:
        vault.read("deploy/notes")
    except KeyError as exc:
        print(f"read back: KeyError {exc} -- nothing was stored")
    vault.close()


if __name__ == "__main__":
    demo_handoff()
    demo_history_rollback()
    demo_conflict()
    demo_redaction()
