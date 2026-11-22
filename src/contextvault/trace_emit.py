"""Emit-side AgentTrace event emission -- optional, off by default.

When a trace ID is supplied -- explicitly to ``ContextVault(trace_id=...)``
or via the environment (``CONTEXTVAULT_TRACE_ID``, falling back to
``AGENTTRACE_TRACE_ID``, the pattern agent-gate established) -- every
store operation appends an AgentTrace schema v1 event to the path in
``CONTEXTVAULT_TRACE_OUT`` (default: ``./context-vault-trace.jsonl``).

Events carry metadata only: key, version numbers, entry kind, agent,
and the SHA-256 of the serialized value. Values themselves are never
emitted -- a trace is readable by the whole fleet, and refused writes
must not leak the very secret that got them refused. The refusal event
carries only the pattern names that fired.

This module is emit-side only: it observes finished store operations
and writes a file. It never feeds back into the store. With no trace
ID configured, nothing is written and behaviour is unchanged.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

SOURCE = "context-vault"
SCHEMA_VERSION = "1"
DEFAULT_OUT = "context-vault-trace.jsonl"


def trace_id_from_env(env: dict | None = None) -> str | None:
    """The externally supplied trace ID, if any (gate's env pattern)."""
    env = os.environ if env is None else env
    return env.get("CONTEXTVAULT_TRACE_ID") or env.get("AGENTTRACE_TRACE_ID") or None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def build_event(trace_id: str, agent: str, type_: str, payload: dict) -> dict:
    """One AgentTrace schema v1 event (pre-store shape: no seq/hash)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "trace_id": trace_id,
        "ts": _now(),
        "source": SOURCE,
        "agent": agent,
        "type": type_,
        "kind": "event",
        "payload": payload,
    }


class TraceEmitter:
    """Appends schema v1 events to a JSONL file when a trace ID is set."""

    def __init__(self, trace_id: str | None = None, out: str | Path | None = None):
        self.trace_id = trace_id if trace_id is not None else trace_id_from_env()
        if out is not None:
            self.out = Path(out)
        else:
            self.out = Path(os.environ.get("CONTEXTVAULT_TRACE_OUT", DEFAULT_OUT))

    @property
    def enabled(self) -> bool:
        return self.trace_id is not None

    def emit(self, agent: str, type_: str, payload: dict) -> Path | None:
        if not self.enabled:
            return None
        event = build_event(self.trace_id, agent, type_, payload)
        with self.out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
        return self.out
