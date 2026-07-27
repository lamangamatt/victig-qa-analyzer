"""Local audit log for QA Analyzer parser calls.

Every LLM call gets logged to SQLite on the Mac mini. No PII is stored
in the log — only:
    - timestamp
    - request hash (opaque; can prove a specific paste was processed
      without exposing the paste)
    - redaction stats (counts by PII kind)
    - model name
    - tokens in / out
    - latency
    - status ("ok" | "error")
    - error message (if any)

Why: FCRA-compliant CRA operations require documented controls over
data sub-processors. This gives us a permanent local record of what
was sent and when, without duplicating the PII itself. Complements
Anthropic's server-side retention (or lack thereof once ZDR is on).

Storage: SQLite at $QA_ANALYZER_AUDIT_DB, defaults to
~/.qa-analyzer-audit.db. Chmod 600 on first create.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


DEFAULT_DB_PATH = Path.home() / ".qa-analyzer-audit.db"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS parse_events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_utc            TEXT    NOT NULL,       -- ISO-8601 UTC timestamp
    request_hash      TEXT    NOT NULL,       -- SHA-256 of ORIGINAL input
    input_bytes       INTEGER NOT NULL,
    redacted_bytes    INTEGER NOT NULL,
    redaction_stats   TEXT    NOT NULL,       -- JSON: counts by kind
    model             TEXT    NOT NULL,
    tokens_in         INTEGER,
    tokens_out        INTEGER,
    latency_ms        INTEGER NOT NULL,
    status            TEXT    NOT NULL,       -- ok | error
    error_message     TEXT,
    parse_confidence  TEXT,                   -- high | medium | low | null
    record_count      INTEGER
);

CREATE INDEX IF NOT EXISTS idx_ts_utc ON parse_events(ts_utc);
CREATE INDEX IF NOT EXISTS idx_status ON parse_events(status);
"""


# ---------------------------------------------------------------------------
# ParseEvent (dataclass for building/logging one row)
# ---------------------------------------------------------------------------


@dataclass
class ParseEvent:
    input_text: str
    redacted_text: str
    redaction_stats: dict
    model: str
    latency_ms: int
    status: str                          # "ok" | "error"
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    error_message: Optional[str] = None
    parse_confidence: Optional[str] = None
    record_count: Optional[int] = None
    ts_utc: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def request_hash(self) -> str:
        h = hashlib.sha256(self.input_text.encode("utf-8", errors="replace"))
        return h.hexdigest()


# ---------------------------------------------------------------------------
# AuditLog (SQLite handle)
# ---------------------------------------------------------------------------


class AuditLog:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(
            path
            or os.environ.get("QA_ANALYZER_AUDIT_DB")
            or DEFAULT_DB_PATH
        ).expanduser().resolve()
        self._init_db()

    # -- setup --------------------------------------------------------------

    def _init_db(self):
        new_file = not self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
        if new_file:
            # Restrict permissions on first create — audit log even sans PII
            # is a compliance artifact; keep it non-world-readable.
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- writes -------------------------------------------------------------

    def log(self, event: ParseEvent) -> int:
        """Insert one parse event; return the new row id."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO parse_events (
                    ts_utc, request_hash, input_bytes, redacted_bytes,
                    redaction_stats, model, tokens_in, tokens_out,
                    latency_ms, status, error_message,
                    parse_confidence, record_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.ts_utc,
                    event.request_hash,
                    len(event.input_text.encode("utf-8", errors="replace")),
                    len(event.redacted_text.encode("utf-8", errors="replace")),
                    json.dumps(event.redaction_stats, sort_keys=True),
                    event.model,
                    event.tokens_in,
                    event.tokens_out,
                    event.latency_ms,
                    event.status,
                    event.error_message,
                    event.parse_confidence,
                    event.record_count,
                ),
            )
            return cur.lastrowid

    # -- reads --------------------------------------------------------------

    def recent(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM parse_events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [_row_to_dict(r) for r in rows]

    def summary(self) -> dict:
        """One-shot roll-up for an admin dashboard view."""
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM parse_events"
            ).fetchone()["n"]
            ok = conn.execute(
                "SELECT COUNT(*) AS n FROM parse_events WHERE status = 'ok'"
            ).fetchone()["n"]
            err = conn.execute(
                "SELECT COUNT(*) AS n FROM parse_events WHERE status = 'error'"
            ).fetchone()["n"]
            avg = conn.execute(
                "SELECT AVG(latency_ms) AS avg_ms FROM parse_events"
                " WHERE status = 'ok'"
            ).fetchone()["avg_ms"]
            first = conn.execute(
                "SELECT ts_utc FROM parse_events ORDER BY id ASC LIMIT 1"
            ).fetchone()
            last = conn.execute(
                "SELECT ts_utc FROM parse_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return {
            "total_events": total,
            "ok_events": ok,
            "error_events": err,
            "avg_latency_ms": round(avg or 0, 1),
            "first_event_utc": first["ts_utc"] if first else None,
            "last_event_utc": last["ts_utc"] if last else None,
            "db_path": str(self.path),
            "db_size_bytes": self.path.stat().st_size if self.path.exists() else 0,
        }


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if isinstance(d.get("redaction_stats"), str):
        try:
            d["redaction_stats"] = json.loads(d["redaction_stats"])
        except json.JSONDecodeError:
            pass
    return d


# ---------------------------------------------------------------------------
# Convenience: module-level singleton (opt-in)
# ---------------------------------------------------------------------------

_default_log: Optional[AuditLog] = None


def default_log() -> AuditLog:
    global _default_log
    if _default_log is None:
        _default_log = AuditLog()
    return _default_log


# ---------------------------------------------------------------------------
# Timer helper
# ---------------------------------------------------------------------------


class Timer:
    """Cheap millisecond wall-clock timer."""

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *a):
        self.elapsed_ms = int((time.perf_counter() - self._start) * 1000)
