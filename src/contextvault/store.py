"""ContextVault: a versioned, provenance-carrying context store.

Multi-agent systems share state by passing messages and hoping. Context
that matters -- a fact one agent established, a decision another made,
an artifact a third produced -- deserves a store with rules:

* **Provenance on every version.** Each version records which agent
  wrote it, when, and the source event it derived from (a run ID, a
  tool-call ID, a ticket -- whatever identifies the origin). A version
  without an author and a source is not accepted.
* **Nothing is silently overwritten.** An update creates a new version;
  the old one stays, readable forever. Readers follow the head or pin
  an exact version. Rollback is a new version pointing back, not a
  rewrite of history.
* **Stale-base writes are conflicts, not merges.** A writer declares
  the version it based its update on (``base_version`` -- the head it
  read). If the head has moved since, the write is stored as a
  *conflicted* version, the head does not move, and ``ConflictError``
  is raised carrying both sides. Both versions are preserved; a human
  or a later write resolves it explicitly.
* **Secrets are refused at the door.** Values are screened for
  secret/PII patterns before anything is persisted (see redaction.py).

Storage is one SQLite file (stdlib ``sqlite3``). Local-first: no
server, no external services.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .redaction import RedactionError, screen
from .trace_emit import TraceEmitter

ENTRY_KINDS = ("fact", "decision", "artifact")

STATUS_CURRENT = "current"
STATUS_SUPERSEDED = "superseded"
STATUS_CONFLICTED = "conflicted"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS versions (
    id           INTEGER PRIMARY KEY,
    key          TEXT NOT NULL,
    version      INTEGER NOT NULL,
    kind         TEXT NOT NULL,
    value        TEXT NOT NULL,
    value_hash   TEXT NOT NULL,
    agent        TEXT NOT NULL,
    ts           TEXT NOT NULL,
    source_event TEXT NOT NULL,
    base_version INTEGER,
    status       TEXT NOT NULL,
    note         TEXT,
    UNIQUE (key, version)
);
CREATE TABLE IF NOT EXISTS heads (
    key          TEXT PRIMARY KEY,
    head_version INTEGER NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _hash(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class Entry:
    """One version of one context entry, with its provenance."""

    key: str
    version: int
    kind: str
    value: object
    agent: str
    ts: str
    source_event: str
    base_version: int | None
    status: str
    note: str | None = None

    def __str__(self) -> str:
        return (
            f"{self.key} v{self.version} [{self.kind}/{self.status}] "
            f"by {self.agent} at {self.ts} (source: {self.source_event}, "
            f"base: {self.base_version}) = {self.value!r}"
        )


class ConflictError(ValueError):
    """A write was based on a stale head. The write was preserved as a
    conflicted version; the head did not move."""

    def __init__(self, key: str, *, base_version: int | None,
                 head_version: int, attempted_version: int):
        self.key = key
        self.base_version = base_version
        self.head_version = head_version
        self.attempted_version = attempted_version
        super().__init__(
            f"write to {key!r} conflicted: based on v{base_version}, but "
            f"head is v{head_version}; stored as conflicted "
            f"v{attempted_version}, head unchanged"
        )


class ContextVault:
    """A versioned shared-context store backed by one SQLite file."""

    def __init__(self, path: str | Path = "context-vault.db", *,
                 trace_id: str | None = None, trace_out=None):
        self._db = sqlite3.connect(str(path))
        self._db.executescript(_SCHEMA)
        self._trace = TraceEmitter(trace_id=trace_id, out=trace_out)

    def close(self) -> None:
        self._db.close()

    # -- writes ------------------------------------------------------

    def write(self, key: str, value, *, agent: str, source_event: str,
              kind: str = "fact", base_version: int | None = None) -> Entry:
        """Write a new version of `key`.

        `base_version` is the head version the writer read before
        writing (None when creating a key). If the head has moved past
        `base_version`, the write is preserved as a conflicted version
        and ConflictError is raised. Raises RedactionError -- storing
        nothing -- if the value matches a secret/PII pattern.
        """
        if kind not in ENTRY_KINDS:
            raise ValueError(f"kind must be one of {ENTRY_KINDS}, got {kind!r}")
        if not agent or not source_event:
            raise ValueError("provenance is mandatory: agent and source_event "
                             "must both be non-empty")
        try:
            screen(key, value)
        except RedactionError as exc:
            # The refusal is itself an auditable event -- pattern names
            # only, never the matched text.
            self._trace.emit(agent, "context_write_refused", {
                "key": key, "patterns": exc.patterns,
            })
            raise

        head = self.head_version(key)
        if head is not None and base_version != head:
            version = self._next_version(key)
            self._insert(key, version, kind, value, agent, source_event,
                         base_version, STATUS_CONFLICTED, note=None)
            self._trace.emit(agent, "context_conflict", {
                "key": key, "version": version,
                "base_version": base_version, "head_version": head,
                "value_hash": _hash(value),
            })
            raise ConflictError(key, base_version=base_version,
                                head_version=head, attempted_version=version)
        version = self._next_version(key)
        if head is not None:
            self._db.execute(
                "UPDATE versions SET status = ? WHERE key = ? AND version = ?",
                (STATUS_SUPERSEDED, key, head))
        entry = self._insert(key, version, kind, value, agent, source_event,
                             base_version, STATUS_CURRENT, note=None)
        self._db.execute(
            "INSERT OR REPLACE INTO heads (key, head_version) VALUES (?, ?)",
            (key, version))
        self._db.commit()
        self._trace.emit(agent, "context_write", {
            "key": key, "version": entry.version, "kind": kind,
            "base_version": base_version, "value_hash": _hash(value),
        })
        return entry

    def rollback(self, key: str, to_version: int, *, agent: str,
                 source_event: str) -> Entry:
        """Roll `key` back to a pinned version -- as a NEW version whose
        value equals the target's. History is never rewritten."""
        target = self.read(key, version=to_version)
        head = self.head_version(key)
        version = self._next_version(key)
        self._db.execute(
            "UPDATE versions SET status = ? WHERE key = ? AND version = ?",
            (STATUS_SUPERSEDED, key, head))
        entry = self._insert(key, version, target.kind, target.value, agent,
                             source_event, head, STATUS_CURRENT,
                             note=f"rollback_of:{to_version}")
        self._db.execute(
            "INSERT OR REPLACE INTO heads (key, head_version) VALUES (?, ?)",
            (key, version))
        self._db.commit()
        self._trace.emit(agent, "context_rollback", {
            "key": key, "version": version, "rollback_of": to_version,
            "value_hash": _hash(target.value),
        })
        return entry

    # -- reads -------------------------------------------------------

    def read(self, key: str, *, version: int | None = None) -> Entry:
        """Read `key`: the head version, or an exact pinned version."""
        if version is None:
            version = self.head_version(key)
            if version is None:
                raise KeyError(f"no such key: {key!r}")
        row = self._db.execute(
            "SELECT key, version, kind, value, agent, ts, source_event,"
            " base_version, status, note FROM versions"
            " WHERE key = ? AND version = ?", (key, version)).fetchone()
        if row is None:
            raise KeyError(f"no version {version} for key {key!r}")
        return self._row(row)

    def history(self, key: str) -> list[Entry]:
        """Every version of `key`, oldest first -- conflicted included."""
        rows = self._db.execute(
            "SELECT key, version, kind, value, agent, ts, source_event,"
            " base_version, status, note FROM versions"
            " WHERE key = ? ORDER BY version", (key,)).fetchall()
        return [self._row(r) for r in rows]

    def head_version(self, key: str) -> int | None:
        row = self._db.execute(
            "SELECT head_version FROM heads WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def keys(self) -> list[str]:
        return [r[0] for r in self._db.execute(
            "SELECT key FROM heads ORDER BY key").fetchall()]

    # -- internals ---------------------------------------------------

    def _next_version(self, key: str) -> int:
        row = self._db.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM versions WHERE key = ?",
            (key,)).fetchone()
        return row[0]

    def _insert(self, key, version, kind, value, agent, source_event,
                base_version, status, note) -> Entry:
        ts = _now()
        serialized = json.dumps(value, default=str)
        self._db.execute(
            "INSERT INTO versions (key, version, kind, value, value_hash,"
            " agent, ts, source_event, base_version, status, note)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (key, version, kind, serialized, _hash(value), agent, ts,
             source_event, base_version, status, note))
        self._db.commit()
        return Entry(key=key, version=version, kind=kind, value=value,
                     agent=agent, ts=ts, source_event=source_event,
                     base_version=base_version, status=status, note=note)

    @staticmethod
    def _row(row) -> Entry:
        key, version, kind, value, agent, ts, source_event, base, status, note = row
        return Entry(key=key, version=version, kind=kind,
                     value=json.loads(value), agent=agent, ts=ts,
                     source_event=source_event, base_version=base,
                     status=status, note=note)
