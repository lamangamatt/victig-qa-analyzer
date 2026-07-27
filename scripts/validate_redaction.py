#!/usr/bin/env python3
"""Redaction validator.

Runs a suite of synthetic paste-format criminal record inputs through
the parser TWICE:
    (a) with PII stripping enabled (redact=True), and
    (b) with PII stripping disabled (redact=False).

Diffs the parsed structured output. Any semantic difference between
the two paths means our redaction pipeline is degrading parse quality,
and should be investigated before rolling out to real records.

Exit code:
    0 = all cases pass (no semantic diffs)
    1 = one or more cases show diffs

Usage:
    export ANTHROPIC_API_KEY=...
    python scripts/validate_redaction.py
    python scripts/validate_redaction.py --case-index 3   # single case
    python scripts/validate_redaction.py --verbose        # show diffs
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from copy import deepcopy
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from qa_analyzer import parser  # noqa: E402


# ---------------------------------------------------------------------------
# Test cases (SYNTHETIC \u2014 no real PII)
# ---------------------------------------------------------------------------
# Each case is a raw paste string that a QA operator might feed into
# the app. Names / DOBs / SSNs / addresses are all invented. We use a
# variety of formats to stress-test the redactor.

TEST_CASES: list[tuple[str, str]] = [
    (
        "labeled_felony_conviction",
        """CANDIDATE:
Name: Marcus Aurelius Jackson
DOB: 03/14/1988
SSN: XXX-XX-4567
Address: 4421 Oak Ridge Ave, Denver, CO 80202
Gender: Male
NameGrade: 42

CRIMINAL RECORD:
Source: Denver County Court (verified)
Case Number: 2020CR001234
Charge: Aggravated Assault (Felony)
Court: Denver County District Court
Arrest Date: 07/15/2020
Filed: 07/20/2020
Disposition: Convicted
Disposition Date: 02/10/2021
Defendant Name: Marcus A Jackson
Defendant DOB: 03/14/1988
Gender: M
""",
    ),
    (
        "misdemeanor_dismissed",
        """Subject: Amelia Rose Chen
Date of Birth: 11/22/1995
SSN: 987-65-4321
Prior Address: 88 Willow Lane, Portland, OR 97201

Record:
Case: 21-MD-9988
Court: Multnomah County Circuit Court, OR
Arrest: 08/03/2021
Charge: Petty Theft - Misdemeanor
Disposition: Dismissed
Disposition Date: 11/15/2021
""",
    ),
    (
        "pending_case_current",
        """Applicant: Theodore Alonzo Ramirez
DOB: 5/1/1982
Social Security: XXX-XX-8899
Address: 1230 Sunset Blvd Apt 4B, Austin, TX 78701
Phone: (512) 555-9876
Email: t.ramirez@sample.org

Charge: DUI (Class B Misdemeanor)
Docket No.: TR-2024-4455
Court: Travis County Court at Law
Arrest Date: 06/12/2024
Disposition: Pending
""",
    ),
    (
        "inverted_name_format",
        """Court Record:
JOHNSON, ELIZABETH M vs. State of California

DOB: October 5, 1976
Address: 512 Elm Court, Sacramento, CA 95814

Case Number: SC-2019-887766
Charge: Grand Larceny (Felony)
Arrest: 04/18/2019
Filed: 04/22/2019
Disposition: Guilty
Sentenced: 09/12/2019
Release Date: 03/15/2022
""",
    ),
    (
        "multi_record_same_person",
        """Name: Kevin Michael O'Brien
DOB: 07/07/1990
SSN: XXX-XX-1111
Address: 200 State St, Boston, MA 02109

Record 1:
Case No.: 22-CR-01
Charge: Assault (Misdemeanor)
Court: Suffolk County District Court
Arrest: 01/15/2022
Disposition: Dismissed
Disp Date: 06/20/2022

Record 2:
Case No.: 22-CR-88
Charge: Disorderly Conduct
Court: Suffolk County District Court
Arrest: 09/03/2022
Disposition: Convicted
Disp Date: 12/10/2022
""",
    ),
    (
        "narrative_prose",
        """On March 15, 2023, David Robert Miller (DOB 06/22/1985) was arrested by
officers of the Cook County Sheriff's Office in Chicago, IL. He was
charged with Possession of a Controlled Substance under Case Number
23-CR-4567 at the Cook County Circuit Court. Miller pleaded guilty and
was convicted on June 30, 2023.

His last known address is 1547 North Damen Ave, Chicago, IL 60622.
SSN on file: XXX-XX-3344.
""",
    ),
]


# ---------------------------------------------------------------------------
# Diff logic
# ---------------------------------------------------------------------------

# Fields we consider SEMANTICALLY IMPORTANT for the deterministic
# analyzer. Differences in these fields between the redacted and
# non-redacted parse are RED FLAGS.
_IMPORTANT_RECORD_FIELDS = [
    "charge_description",
    "offense_level",
    "disposition",
    "state",
    "county",
    "arrest_date",
    "file_date",
    "disposition_date",
    "release_date",
    "case_number",
    "record_id",
    "source_confirmed",
    "is_marijuana_possession",
]

_IMPORTANT_SUBJECT_FIELDS = [
    "first_name",
    "last_name",
    "middle_name",
    "dob",
    "ssn_last4",
    "gender",
    "name_grade",
]


def _normalize_dates(value):
    """Some date fields come back as 'YYYY-MM-DD' after parsing while
    the input used slashes. Normalize before diffing so we don't false-
    positive on cosmetic diffs."""
    if not isinstance(value, str):
        return value
    v = value.strip().replace("/", "-")
    parts = v.split("-")
    if len(parts) == 3:
        # Try to canonicalize to YYYY-MM-DD
        if len(parts[0]) == 4:
            try:
                return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
            except ValueError:
                return v
        if len(parts[2]) == 4:
            try:
                return f"{int(parts[2]):04d}-{int(parts[0]):02d}-{int(parts[1]):02d}"
            except ValueError:
                return v
    return v


def _normalize(v):
    if isinstance(v, str):
        return v.strip()
    return v


_NAME_FIELDS = {"first_name", "last_name", "middle_name",
                "record_first_name", "record_last_name", "record_middle_name"}
_DATE_FIELDS_ANY = {"dob", "arrest_date", "file_date",
                    "disposition_date", "release_date", "record_dob"}


def _norm_for_compare(field: str, value):
    """Normalize a field value for cross-mode comparison.

    Case is folded for name fields (redaction path normalizes casing
    on inverted names like "JOHNSON, ELIZABETH" → "Johnson"; Claude
    without redaction may preserve the raw uppercase). Dates are ISO-
    normalized. Everything else is stripped and lowered for robustness.
    """
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        if field in _NAME_FIELDS:
            return v.casefold()
        if field in _DATE_FIELDS_ANY:
            n = _normalize_dates(v)
            return n if n else v
        return v
    return value


def _compare_subject(a: dict, b: dict) -> list[str]:
    diffs = []
    for field in _IMPORTANT_SUBJECT_FIELDS:
        av = _norm_for_compare(field, a.get(field))
        bv = _norm_for_compare(field, b.get(field))
        if av != bv:
            diffs.append(f"subject.{field}: {av!r} vs {bv!r}")
    return diffs


def _compare_records(a: list, b: list) -> list[str]:
    diffs = []
    if len(a) != len(b):
        diffs.append(f"record_count: {len(a)} vs {len(b)}")
        return diffs
    for i, (ra, rb) in enumerate(zip(a, b)):
        for field in _IMPORTANT_RECORD_FIELDS:
            av = _norm_for_compare(field, ra.get(field))
            bv = _norm_for_compare(field, rb.get(field))
            if av != bv:
                diffs.append(f"records[{i}].{field}: {av!r} vs {bv!r}")
    return diffs


def diff_parses(with_redaction: dict, without_redaction: dict) -> list[str]:
    diffs = []
    diffs.extend(_compare_subject(
        with_redaction.get("subject", {}),
        without_redaction.get("subject", {}),
    ))
    diffs.extend(_compare_records(
        with_redaction.get("records", []),
        without_redaction.get("records", []),
    ))
    return diffs


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_case(name: str, text: str, verbose: bool = False) -> tuple[bool, list[str]]:
    """Parse a case twice and diff. Returns (ok, diffs)."""
    # Turn OFF audit logging for the validator; we don't want to pollute
    # the log with synthetic-data noise.
    try:
        parsed_red = parser.parse(text, redact=True, log=False)
    except parser.ParserError as e:
        return False, [f"parse with redaction FAILED: {e}"]

    try:
        parsed_raw = parser.parse(text, redact=False, log=False)
    except parser.ParserError as e:
        return False, [f"parse without redaction FAILED: {e}"]

    diffs = diff_parses(parsed_red, parsed_raw)
    if verbose:
        print()
        print(f"    Redacted:  {json.dumps(parsed_red.get('subject'), indent=2)[:400]}")
        print(f"    Raw:       {json.dumps(parsed_raw.get('subject'), indent=2)[:400]}")

    return (len(diffs) == 0), diffs


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--case-index", type=int, help="Run only case N (0-based)")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set.")
        print("Try: source ~/.qa-analyzer.env")
        sys.exit(2)

    cases = TEST_CASES
    if args.case_index is not None:
        cases = [TEST_CASES[args.case_index]]

    print(f"Running {len(cases)} case(s) through parser (redact=True vs False)…")
    print()

    failures = []
    for i, (name, text) in enumerate(cases):
        print(f"  [{i}] {name} ... ", end="", flush=True)
        ok, diffs = run_case(name, text, verbose=args.verbose)
        if ok:
            print("PASS")
        else:
            print("DIFFERS")
            for d in diffs:
                print(f"        - {d}")
            failures.append((name, diffs))

    print()
    print("=" * 70)
    if not failures:
        print(f"\u2705  ALL {len(cases)} CASES PASSED")
        print("Redaction pipeline preserves parse semantics.")
        sys.exit(0)
    else:
        print(f"\u274c  {len(failures)} of {len(cases)} CASES SHOW DIFFS")
        for name, diffs in failures:
            print(f"  {name}:")
            for d in diffs:
                print(f"    - {d}")
        sys.exit(1)


if __name__ == "__main__":
    main()
