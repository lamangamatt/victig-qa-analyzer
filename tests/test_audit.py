"""Unit tests for the audit log."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import pytest  # noqa: E402

from qa_analyzer.audit import AuditLog, ParseEvent, Timer  # noqa: E402


@pytest.fixture
def tmp_log(tmp_path):
    return AuditLog(path=tmp_path / "test-audit.db")


def _make_event(**overrides) -> ParseEvent:
    defaults = dict(
        input_text="Name: John Smith\nDOB: 1985-01-01",
        redacted_text="Name: Aria Ashford\nDOB: 1900-01-01",
        redaction_stats={"name": 1, "dob": 1},
        model="claude-haiku-4-5",
        latency_ms=234,
        status="ok",
        tokens_in=120,
        tokens_out=340,
        parse_confidence="high",
        record_count=1,
    )
    defaults.update(overrides)
    return ParseEvent(**defaults)


def test_log_and_read_back(tmp_log):
    event = _make_event()
    row_id = tmp_log.log(event)
    assert row_id > 0

    rows = tmp_log.recent(limit=10)
    assert len(rows) == 1
    r = rows[0]
    assert r["status"] == "ok"
    assert r["model"] == "claude-haiku-4-5"
    assert r["tokens_in"] == 120
    assert r["latency_ms"] == 234
    assert r["parse_confidence"] == "high"
    assert r["record_count"] == 1
    # No PII stored
    assert "John" not in str(r)
    assert "Smith" not in str(r)


def test_input_bytes_and_redacted_bytes_recorded(tmp_log):
    event = _make_event()
    tmp_log.log(event)
    r = tmp_log.recent()[0]
    assert r["input_bytes"] == len(event.input_text.encode("utf-8"))
    assert r["redacted_bytes"] == len(event.redacted_text.encode("utf-8"))


def test_request_hash_is_stable(tmp_log):
    e1 = _make_event()
    e2 = _make_event()
    tmp_log.log(e1)
    tmp_log.log(e2)
    rows = tmp_log.recent()
    assert rows[0]["request_hash"] == rows[1]["request_hash"]
    # Sanity: it's a hex SHA-256
    assert len(rows[0]["request_hash"]) == 64
    assert all(c in "0123456789abcdef" for c in rows[0]["request_hash"])


def test_request_hash_differs_for_different_input(tmp_log):
    tmp_log.log(_make_event(input_text="Case A"))
    tmp_log.log(_make_event(input_text="Case B"))
    rows = tmp_log.recent()
    assert rows[0]["request_hash"] != rows[1]["request_hash"]


def test_error_event(tmp_log):
    event = _make_event(status="error", error_message="rate limit exceeded")
    tmp_log.log(event)
    r = tmp_log.recent()[0]
    assert r["status"] == "error"
    assert "rate limit" in r["error_message"]


def test_summary_reports_totals(tmp_log):
    for _ in range(3):
        tmp_log.log(_make_event(latency_ms=100))
    for _ in range(2):
        tmp_log.log(_make_event(status="error", error_message="boom",
                                latency_ms=50))

    s = tmp_log.summary()
    assert s["total_events"] == 5
    assert s["ok_events"] == 3
    assert s["error_events"] == 2
    assert s["avg_latency_ms"] == 100.0  # avg over ok events only


def test_redaction_stats_roundtrip(tmp_log):
    stats = {"name": 2, "dob": 1, "ssn": 1, "case": 1}
    tmp_log.log(_make_event(redaction_stats=stats))
    r = tmp_log.recent()[0]
    assert r["redaction_stats"] == stats


def test_permissions_on_new_db(tmp_path):
    # Skip on Windows / non-POSIX
    if os.name != "posix":
        pytest.skip("POSIX-only permissions test")
    db_path = tmp_path / "perms.db"
    AuditLog(path=db_path)
    mode = db_path.stat().st_mode & 0o777
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"


def test_timer_measures_elapsed():
    import time
    with Timer() as t:
        time.sleep(0.01)
    assert t.elapsed_ms >= 5
