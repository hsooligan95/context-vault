"""Trace emission: AgentTrace schema v1 events under a propagated ID."""

import json

import pytest

from contextvault import ConflictError, ContextVault, RedactionError


def read_events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture()
def traced(tmp_path):
    out = tmp_path / "trace.jsonl"
    v = ContextVault(tmp_path / "test.db", trace_id="trace-cv-test-1",
                     trace_out=out)
    yield v, out
    v.close()


def test_events_are_schema_v1_and_carry_the_trace_id(traced):
    vault, out = traced
    vault.write("k", {"a": 1}, agent="agent-a", source_event="run-1")
    vault.write("k", {"a": 2}, agent="agent-b", source_event="run-2",
                base_version=1)
    vault.rollback("k", to_version=1, agent="agent-b", source_event="run-3")

    events = read_events(out)
    assert [e["type"] for e in events] == [
        "context_write", "context_write", "context_rollback"]
    for e in events:
        assert e["schema_version"] == "1"
        assert e["trace_id"] == "trace-cv-test-1"
        assert e["source"] == "context-vault"
        assert e["kind"] == "event"
        assert e["ts"] and e["agent"] and isinstance(e["payload"], dict)
    # Values are never emitted -- hashes are.
    assert '"a": 1' not in out.read_text()
    assert events[0]["payload"]["value_hash"]


def test_conflict_and_refusal_are_traced_without_leaking(traced):
    vault, out = traced
    vault.write("w", "Sat", agent="planner", source_event="e1")
    with pytest.raises(ConflictError):
        vault.write("w", "Sun", agent="ops", source_event="e2",
                    base_version=99)
    with pytest.raises(RedactionError):
        vault.write("s", "AKIAIOSFODNN7EXAMPLE", agent="ops",
                    source_event="e3")

    events = read_events(out)
    assert [e["type"] for e in events] == [
        "context_write", "context_conflict", "context_write_refused"]
    refused = events[2]["payload"]
    assert refused["patterns"] == ["aws_access_key_id"]
    assert "AKIA" not in out.read_text()  # the secret never reaches the trace


def test_no_trace_id_no_file(tmp_path, monkeypatch):
    monkeypatch.delenv("CONTEXTVAULT_TRACE_ID", raising=False)
    monkeypatch.delenv("AGENTTRACE_TRACE_ID", raising=False)
    out = tmp_path / "trace.jsonl"
    v = ContextVault(tmp_path / "test.db", trace_out=out)
    v.write("k", 1, agent="a", source_event="e1")
    v.close()
    assert not out.exists()


def test_trace_id_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTTRACE_TRACE_ID", "trace-from-env")
    out = tmp_path / "trace.jsonl"
    v = ContextVault(tmp_path / "test.db", trace_out=out)
    v.write("k", 1, agent="a", source_event="e1")
    v.close()
    events = read_events(out)
    assert events[0]["trace_id"] == "trace-from-env"
