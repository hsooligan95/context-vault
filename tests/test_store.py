"""Store behaviour: handoff, versioning, rollback, conflicts, provenance."""

import pytest

from contextvault import ConflictError, ContextVault


@pytest.fixture()
def vault(tmp_path):
    v = ContextVault(tmp_path / "test.db")
    yield v
    v.close()


# -- Demo 1: handoff ---------------------------------------------------

def test_two_agents_build_shared_context_across_handoff(vault):
    vault.write("incident/facts", {"pump": "P-114", "symptom": "cavitation"},
                agent="diagnostics-agent", kind="fact",
                source_event="run-diag-1")
    vault.write("incident/decision", {"action": "hold production"},
                agent="diagnostics-agent", kind="decision",
                source_event="run-diag-1")

    facts = vault.read("incident/facts")
    assert facts.agent == "diagnostics-agent"

    updated = vault.write(
        "incident/facts",
        {"pump": "P-114", "symptom": "cavitation", "inlet_pressure_psi": 41},
        agent="remediation-agent", kind="fact",
        source_event="run-rem-1", base_version=facts.version)
    assert updated.version == 2
    assert vault.read("incident/facts").value["inlet_pressure_psi"] == 41
    # The first agent's version is still there, untouched.
    assert vault.read("incident/facts", version=1).value == {
        "pump": "P-114", "symptom": "cavitation"}


# -- Demo 2: history, pinning, rollback --------------------------------

def test_history_pinning_and_rollback(vault):
    vault.write("target", 1200, agent="planner", source_event="e1")
    vault.write("target", 1350, agent="planner", source_event="e2",
                base_version=1)
    vault.write("target", 900, agent="ops", source_event="e3", base_version=2)

    history = vault.history("target")
    assert [e.version for e in history] == [1, 2, 3]
    assert [e.status for e in history] == [
        "superseded", "superseded", "current"]
    assert vault.read("target").value == 900
    assert vault.read("target", version=1).value == 1200  # pinned read

    rolled = vault.rollback("target", to_version=1, agent="ops",
                            source_event="e4")
    assert rolled.version == 4
    assert rolled.note == "rollback_of:1"
    assert vault.read("target").value == 1200
    # Rollback did not rewrite history: all four versions still exist.
    assert len(vault.history("target")) == 4


# -- Demo 3: stale-base conflict ---------------------------------------

def test_stale_base_write_is_flagged_and_preserved(vault):
    vault.write("window", "Sat 02:00", agent="planner", kind="decision",
                source_event="e1")
    vault.write("window", "Sat 03:00", agent="scheduler", kind="decision",
                source_event="e2", base_version=1)

    with pytest.raises(ConflictError) as excinfo:
        vault.write("window", "Sun 01:00", agent="ops", kind="decision",
                    source_event="e3", base_version=1)
    exc = excinfo.value
    assert exc.base_version == 1 and exc.head_version == 2
    assert exc.attempted_version == 3

    # Head did not move; the conflicting write is preserved, flagged.
    assert vault.read("window").value == "Sat 03:00"
    history = vault.history("window")
    assert [e.status for e in history] == [
        "superseded", "current", "conflicted"]
    assert vault.read("window", version=3).value == "Sun 01:00"

    # Resolution is an explicit new write based on the real head.
    resolved = vault.write("window", "Sat 03:30", agent="ops",
                           kind="decision", source_event="e4", base_version=2)
    assert resolved.version == 4 and resolved.status == "current"
    assert vault.read("window").value == "Sat 03:30"


def test_write_to_existing_key_without_base_conflicts(vault):
    vault.write("k", "a", agent="a1", source_event="e1")
    with pytest.raises(ConflictError):
        vault.write("k", "b", agent="a2", source_event="e2")


# -- Provenance ----------------------------------------------------------

def test_every_version_is_attributable(vault):
    vault.write("k", 1, agent="agent-a", source_event="run-1")
    vault.write("k", 2, agent="agent-b", source_event="run-2", base_version=1)
    with pytest.raises(ConflictError):
        vault.write("k", 3, agent="agent-c", source_event="run-3",
                    base_version=1)
    vault.rollback("k", to_version=1, agent="agent-b", source_event="run-4")

    for entry in vault.history("k"):
        assert entry.agent, f"v{entry.version} has no agent"
        assert entry.source_event, f"v{entry.version} has no source event"
        assert entry.ts, f"v{entry.version} has no timestamp"
    authors = {e.version: (e.agent, e.source_event)
               for e in vault.history("k")}
    assert authors[1] == ("agent-a", "run-1")
    assert authors[3] == ("agent-c", "run-3")  # even the conflicted write


def test_provenance_is_mandatory(vault):
    with pytest.raises(ValueError):
        vault.write("k", 1, agent="", source_event="run-1")
    with pytest.raises(ValueError):
        vault.write("k", 1, agent="agent-a", source_event="")


def test_missing_key_and_version_raise(vault):
    with pytest.raises(KeyError):
        vault.read("nope")
    vault.write("k", 1, agent="a", source_event="e1")
    with pytest.raises(KeyError):
        vault.read("k", version=99)


def test_invalid_kind_rejected(vault):
    with pytest.raises(ValueError):
        vault.write("k", 1, agent="a", source_event="e1", kind="rumor")
