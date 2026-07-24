"""Test 1 — Is this a reportable offense level?

SOP §5 test 1, p.11: "Felonies/Misdemeanors or state equivalent"
SOP §18: Non-reportable levels include traffic infractions, minor/petty
misdemeanors, ordinances, WI Forfeiture U, PA Summary Offenses, NJ Petty
Disorderly Persons.

Also handles the Indiana-specific rule (§7): Class D / Level 6 Felony
amended to Class A Misdemeanor is NOT reportable as felony (misdemeanor
form CAN be reported).
"""

from __future__ import annotations

from qa_analyzer.models import CriminalRecord, OffenseLevel, RuleResult


REPORTABLE_LEVELS = {OffenseLevel.FELONY, OffenseLevel.MISDEMEANOR}


def evaluate(record: CriminalRecord) -> RuleResult:
    """Determine if the offense level itself is reportable."""

    level = record.offense_level

    # Unknown level → escalate (fail-safe)
    if level == OffenseLevel.UNKNOWN:
        return RuleResult(
            test_id="T1_reportable_offense",
            test_name="Reportable Offense Level",
            passed=False,
            detail=(
                "Offense level is UNKNOWN. Cannot determine reportability "
                "without classification. Escalate for human review."
            ),
            sop_reference="SOP §5 test 1, p.11",
            escalate=True,
        )

    # Indiana Class D/Level 6 Felony amended to Misdemeanor rule
    if (
        record.state.upper() == "IN"
        and level == OffenseLevel.FELONY
        and record.is_amended_from_felony
    ):
        return RuleResult(
            test_id="T1_reportable_offense",
            test_name="Reportable Offense Level",
            passed=False,
            detail=(
                "Indiana rule: Class D / Level 6 Felony amended to Class A "
                "Misdemeanor is NOT reportable as a felony. Report the "
                "misdemeanor version separately if applicable."
            ),
            sop_reference="SOP §7 p.14 (Indiana)",
        )

    if level in REPORTABLE_LEVELS:
        return RuleResult(
            test_id="T1_reportable_offense",
            test_name="Reportable Offense Level",
            passed=True,
            detail=f"Offense level '{level.value}' is a reportable classification.",
            sop_reference="SOP §5 test 1, p.11",
        )

    # Non-reportable level (petty/ordinance/etc.)
    reason_map = {
        OffenseLevel.PETTY_MISDEMEANOR: "'Petty Misdemeanor' (e.g., MN)",
        OffenseLevel.MINOR_MISDEMEANOR: "'Minor Misdemeanor' (e.g., OH)",
        OffenseLevel.TRAFFIC_INFRACTION: "Traffic infraction (petty offense)",
        OffenseLevel.ORDINANCE: "Ordinance (noise/dogs/littering)",
        OffenseLevel.FORFEITURE_U: "Wisconsin Forfeiture U",
        OffenseLevel.SUMMARY_OFFENSE: "Pennsylvania Summary Offense",
        OffenseLevel.PETTY_DISORDERLY: "New Jersey Petty Disorderly Persons Offense",
    }
    label = reason_map.get(level, level.value)

    return RuleResult(
        test_id="T1_reportable_offense",
        test_name="Reportable Offense Level",
        passed=False,
        detail=(
            f"Offense level '{label}' is non-reportable per SOP §18. "
            "Only felonies and misdemeanors (or state equivalents) may be reported."
        ),
        sop_reference="SOP §5 test 1, p.11 + §18",
    )
