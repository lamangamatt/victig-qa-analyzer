"""Test 4 — Sufficient PII to match the record to the candidate?

SOP §5 test 4, p.11:

    Utilize the NameGrade tool to determine name commonality threshold
    (58 or above).

    i. NameGrade BELOW threshold:
       At least TWO (2) identifiers such as FULL DOB & First & Last Name.

    ii. NameGrade ABOVE threshold:
       At least THREE (3) identifiers such as FULL DOB, Middle Name/Initial
       & First & Last Name.

⚠️ NAMEGRADE THRESHOLD DISCREPANCY:
    - p.11 (operational): "58 or above"
    - p.17 (algorithm description): "above 56"
    Configurable via NAMEGRADE_THRESHOLD below; default = 58.

This test leverages the matching-policy engine to compute Level 1 matches.
"""

from __future__ import annotations

from qa_analyzer.matching import (
    NAMEGRADE_THRESHOLD,
    level_one_matches,
    level_two_disqualifiers,
    level_three_flags,
)
from qa_analyzer.models import CriminalRecord, RuleResult, Subject


def evaluate(record: CriminalRecord, subject: Subject) -> RuleResult:
    """Determine if PII is sufficient for a defensible match."""

    l2_disqualifiers = level_two_disqualifiers(record, subject)
    if l2_disqualifiers:
        return RuleResult(
            test_id="T4_pii_match",
            test_name="Sufficient PII to Match",
            passed=False,
            detail=(
                "Level 2 disqualifier(s): "
                + "; ".join(l2_disqualifiers)
                + ". Record cannot belong to this subject."
            ),
            sop_reference="SOP §8 Matching Policy (Level Two), p.16",
        )

    l1_matches = level_one_matches(record, subject)
    match_count = sum(1 for m in l1_matches.values() if m is True)

    name_grade = subject.name_grade
    if name_grade is None:
        # SOP requires NameGrade to be run. If missing, escalate.
        return RuleResult(
            test_id="T4_pii_match",
            test_name="Sufficient PII to Match",
            passed=False,
            detail=(
                "Subject's NameGrade™ score is missing. SOP §5 test 4 "
                "requires the NameGrade tool to determine required "
                "identifier threshold. Escalate to run NameGrade."
            ),
            sop_reference="SOP §5 test 4, p.11",
            escalate=True,
        )

    common_name = name_grade >= NAMEGRADE_THRESHOLD
    required = 3 if common_name else 2
    threshold_note = (
        f"NameGrade={name_grade} "
        f"({'≥' if common_name else '<'}{NAMEGRADE_THRESHOLD}, "
        f"{'COMMON name — need 3 identifiers' if common_name else 'uncommon — need 2 identifiers'})"
    )

    l3_flags = level_three_flags(record, subject)

    if match_count >= required:
        detail = (
            f"{match_count} of 4 Level 1 identifiers match "
            f"({', '.join(k for k, v in l1_matches.items() if v)}). "
            f"{threshold_note}."
        )

        # SOP §8: the specific case that requires ≥2 supervisor approval
        # is a COMMON NAME with NO locational match. Other L3 flags are
        # informational and don't force escalation.
        no_locational_match = any(
            "does not appear in subject's address history" in f
            or "no address history to compare" in f
            for f in l3_flags
        )
        if common_name and no_locational_match:
            return RuleResult(
                test_id="T4_pii_match",
                test_name="Sufficient PII to Match",
                passed=False,
                detail=(
                    detail
                    + " HOWEVER: common name with no locational match. "
                    "SOP §8 requires review by ≥2 supervisors. Flags: "
                    + "; ".join(l3_flags)
                ),
                sop_reference="SOP §8 Matching Policy (Level Three), p.16",
                escalate=True,
            )
        if l3_flags:
            # Warn but pass — flags are informational scrutiny prompts.
            return RuleResult(
                test_id="T4_pii_match",
                test_name="Sufficient PII to Match",
                passed=True,
                detail=(
                    detail
                    + " Level 3 red flag(s) noted for reviewer awareness: "
                    + "; ".join(l3_flags)
                ),
                sop_reference="SOP §5 test 4, p.11 + §8, p.16",
            )
        return RuleResult(
            test_id="T4_pii_match",
            test_name="Sufficient PII to Match",
            passed=True,
            detail=detail,
            sop_reference="SOP §5 test 4, p.11 + §8, p.16",
        )

    return RuleResult(
        test_id="T4_pii_match",
        test_name="Sufficient PII to Match",
        passed=False,
        detail=(
            f"Only {match_count} of 4 Level 1 identifiers match "
            f"({', '.join(k for k, v in l1_matches.items() if v) or 'none'}); "
            f"need {required}. {threshold_note}."
        ),
        sop_reference="SOP §5 test 4, p.11",
    )
