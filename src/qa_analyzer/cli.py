"""Command-line interface for the QA analyzer.

Usage:
    python3 -m qa_analyzer.cli analyze <case.json>
    python3 -m qa_analyzer.cli batch <cases.json> [--output results.json]

The JSON schema is documented in examples/README.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields, is_dataclass
from datetime import date
from pathlib import Path
from typing import Any, Optional

from qa_analyzer.decision import analyze_record
from qa_analyzer.models import (
    ClientProfile,
    CriminalRecord,
    Decision,
    Disposition,
    Gender,
    OffenseLevel,
    Subject,
)


# ---------------------------------------------------------------------------
# JSON loaders
# ---------------------------------------------------------------------------


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _parse_enum(cls, value: Optional[str]):
    if value is None:
        return getattr(cls, "UNKNOWN", None)
    return cls(value)


def load_subject(data: dict) -> Subject:
    return Subject(
        first_name=data["first_name"],
        last_name=data["last_name"],
        middle_name=data.get("middle_name"),
        dob=_parse_date(data.get("dob")),
        ssn_last4=data.get("ssn_last4"),
        gender=_parse_enum(Gender, data.get("gender")),
        race=data.get("race"),
        address_history=data.get("address_history", []),
        annual_salary=data.get("annual_salary"),
        name_grade=data.get("name_grade"),
    )


def load_record(data: dict) -> CriminalRecord:
    return CriminalRecord(
        record_id=data["record_id"],
        source=data.get("source", ""),
        source_confirmed=data.get("source_confirmed", False),
        charge_description=data.get("charge_description", ""),
        offense_level=_parse_enum(OffenseLevel, data.get("offense_level", "unknown")),
        disposition=_parse_enum(Disposition, data.get("disposition", "unknown")),
        offense_date=_parse_date(data.get("offense_date")),
        arrest_date=_parse_date(data.get("arrest_date")),
        file_date=_parse_date(data.get("file_date")),
        disposition_date=_parse_date(data.get("disposition_date")),
        release_date=_parse_date(data.get("release_date")),
        parole_start_date=_parse_date(data.get("parole_start_date")),
        sentence_max_date=_parse_date(data.get("sentence_max_date")),
        state=data.get("state", ""),
        county=data.get("county", ""),
        court_name=data.get("court_name", ""),
        case_number=data.get("case_number", ""),
        record_first_name=data.get("record_first_name", ""),
        record_last_name=data.get("record_last_name", ""),
        record_middle_name=data.get("record_middle_name"),
        record_dob=_parse_date(data.get("record_dob")),
        record_ssn_last4=data.get("record_ssn_last4"),
        record_gender=_parse_enum(Gender, data.get("record_gender")),
        record_race=data.get("record_race"),
        record_address_state=data.get("record_address_state"),
        is_marijuana_possession=data.get("is_marijuana_possession", False),
        is_amended_from_felony=data.get("is_amended_from_felony", False),
        is_probation_violation_incarceration=data.get(
            "is_probation_violation_incarceration", False
        ),
    )


def load_client(data: Optional[dict]) -> Optional[ClientProfile]:
    if not data:
        return None
    return ClientProfile(
        client_id=data.get("client_id", ""),
        client_name=data.get("client_name", ""),
        max_years_misdemeanor=data.get("max_years_misdemeanor"),
        max_years_felony=data.get("max_years_felony"),
        felonies_only=data.get("felonies_only", False),
        sex_offenses_only=data.get("sex_offenses_only", False),
        include_offense_keywords=data.get("include_offense_keywords", []),
        exclude_offense_keywords=data.get("exclude_offense_keywords", []),
    )


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

BOX_TOP = "╔" + "═" * 78 + "╗"
BOX_MID = "╠" + "═" * 78 + "╣"
BOX_BOT = "╚" + "═" * 78 + "╝"


def _color(outcome: str) -> str:
    """ANSI colors for terminal output (green/yellow/red)."""
    if not sys.stdout.isatty():
        return outcome
    codes = {"REPORT": "\033[32m", "EXCLUDE": "\033[31m", "ESCALATE": "\033[33m"}
    reset = "\033[0m"
    return f"{codes.get(outcome, '')}{outcome}{reset}"


def format_decision(decision: Decision, record: CriminalRecord, subject: Subject) -> str:
    """Pretty-print a decision for terminal display."""
    lines = []
    lines.append(BOX_TOP)
    header = f" QA ANALYZER — {decision.record_id}"
    lines.append("║" + header.ljust(78) + "║")
    lines.append(BOX_MID)

    subj_parts = [subject.first_name]
    if subject.middle_name:
        subj_parts.append(subject.middle_name)
    subj_parts.append(subject.last_name)
    lines.append(f" Subject: {' '.join(subj_parts)}")
    lines.append(
        f" Charge:  {record.charge_description or '(unspecified)'} "
        f"({record.offense_level.value})"
    )
    lines.append(
        f" Court:   {record.state} {record.county} — {record.court_name} "
        f"[case {record.case_number}]"
    )
    lines.append(f" Disposition: {record.disposition.value}")
    if decision.controlling_date:
        lines.append(
            f" Controlling date: {decision.controlling_date.isoformat()} "
            f"({decision.controlling_date_reason})"
        )
    lines.append("")
    lines.append(f" >>> OUTCOME: {_color(decision.outcome.value)} <<<")
    lines.append("")

    lines.append(" ── Rule results ─────────────────────────────────────────────────────────────")
    for r in decision.rule_results:
        mark = "✓" if r.passed else "✗"
        esc = " ⚠️ ESCALATE" if r.escalate else ""
        lines.append(f"  {mark} [{r.test_id}] {r.test_name}{esc}")
        # Wrap detail lines
        for line in _wrap_text(r.detail, 74, indent="      "):
            lines.append(line)
        if r.sop_reference:
            lines.append(f"      → {r.sop_reference}")
        lines.append("")

    if decision.matching_score:
        lines.append(" ── Matching (SOP §8) ────────────────────────────────────────────────────────")
        m = decision.matching_score
        lines.append(
            f"  Level 1 matches: {m['level_one_match_count']}/4  "
            f"NameGrade: {m['name_grade']} "
            f"(threshold {m['namegrade_threshold']}, "
            f"common={m['common_name']})"
        )
        for k, v in m["level_one_matches"].items():
            symbol = {True: "✓", False: "✗", None: "?"}[v]
            lines.append(f"    {symbol} {k}")
        if m["level_two_disqualifiers"]:
            lines.append("  Level 2 disqualifiers (RECORD EXCLUDED):")
            for d in m["level_two_disqualifiers"]:
                lines.append(f"    ✗ {d}")
        if m["level_three_flags"]:
            lines.append("  Level 3 red flags (require scrutiny):")
            for f in m["level_three_flags"]:
                lines.append(f"    ⚠️ {f}")
        lines.append("")

    if decision.state_rules_applied:
        lines.append(" ── State law overlays ───────────────────────────────────────────────────────")
        for note in decision.state_rules_applied:
            for line in _wrap_text(note, 76, indent="  "):
                lines.append(line)
        lines.append("")

    if decision.warnings:
        lines.append(" ── Warnings ─────────────────────────────────────────────────────────────────")
        for w in decision.warnings:
            for line in _wrap_text(w, 76, indent="  "):
                lines.append(line)
        lines.append("")

    if decision.escalation_reasons:
        lines.append(" ── Escalation reasons ───────────────────────────────────────────────────────")
        for e in decision.escalation_reasons:
            for line in _wrap_text(e, 76, indent="  "):
                lines.append(line)
        lines.append("")

    lines.append(BOX_BOT)
    return "\n".join(lines)


def _wrap_text(text: str, width: int, indent: str = "") -> list[str]:
    """Simple word-wrap for terminal output."""
    words = text.split()
    lines = []
    current = indent
    for w in words:
        if len(current) + len(w) + 1 > width:
            lines.append(current)
            current = indent + w
        else:
            current = current + " " + w if current.strip() else current + w
    if current.strip():
        lines.append(current)
    return lines


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_analyze(args: argparse.Namespace) -> int:
    """Analyze a single case file."""
    with open(args.case_file) as f:
        case = json.load(f)

    subject = load_subject(case["subject"])
    client = load_client(case.get("client"))
    records = [load_record(r) for r in case["records"]]

    for record in records:
        decision = analyze_record(
            record, subject, client, other_records_on_report=records
        )
        if args.json:
            print(json.dumps(decision.to_dict(), indent=2))
        else:
            print(format_decision(decision, record, subject))
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    """Analyze a batch of cases from a JSON file. Writes results to --output."""
    with open(args.case_file) as f:
        cases = json.load(f)

    results = []
    counts = {"REPORT": 0, "EXCLUDE": 0, "ESCALATE": 0}

    for i, case in enumerate(cases):
        subject = load_subject(case["subject"])
        client = load_client(case.get("client"))
        records = [load_record(r) for r in case["records"]]
        case_result = {
            "subject": f"{subject.first_name} {subject.last_name}",
            "decisions": [],
        }
        for record in records:
            decision = analyze_record(
                record, subject, client, other_records_on_report=records
            )
            case_result["decisions"].append(decision.to_dict())
            counts[decision.outcome.value] += 1
            if not args.quiet:
                print(decision.summary())
        results.append(case_result)

    if args.output:
        Path(args.output).write_text(json.dumps(results, indent=2))
        print(f"\nWrote {len(results)} case(s) to {args.output}")

    print(f"\nSummary:")
    for outcome, count in counts.items():
        print(f"  {outcome:10s} {count}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="qa_analyzer",
        description="VICTIG SOP-driven criminal record reportability engine",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_analyze = subparsers.add_parser("analyze", help="Analyze a single case")
    p_analyze.add_argument("case_file", help="Path to case JSON file")
    p_analyze.add_argument("--json", action="store_true",
                           help="Emit raw JSON instead of formatted output")
    p_analyze.set_defaults(func=cmd_analyze)

    p_batch = subparsers.add_parser("batch", help="Analyze a batch of cases")
    p_batch.add_argument("case_file", help="Path to cases JSON file")
    p_batch.add_argument("--output", help="Write full JSON results to this path")
    p_batch.add_argument("--quiet", action="store_true", help="Suppress per-record lines")
    p_batch.set_defaults(func=cmd_batch)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
