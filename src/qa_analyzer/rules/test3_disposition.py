"""Test 3 — Does the record have a reportable disposition?

SOP §5 test 3, p.11: "See the Disposition Jargon spreadsheet for expansion
on what constitutes a conviction, and how VICTIG reports active
non-convictions."

Reportable dispositions:
    - Convictions (guilty, convicted, nolo contendere)
    - Active non-convictions (pending, deferred/diversion in progress)

Non-reportable dispositions:
    - Dismissed / acquitted / not guilty
    - Adjudication withheld (finding of guilt but not a conviction)
    - Deferred that was successfully completed
    - Expunged / sealed
    - Juvenile-court adjudications
    - Arrest-only records (EEOC — never reportable)

Special:
    - Missing disposition → SOP §2 says "assume conviction and follow up"
      (i.e., escalate to research team to obtain disposition from source)
    - IL rule: adjudication withheld/deferred not reportable regardless
      of age IF no other conviction on the report (handled here + globally)
"""

from __future__ import annotations

from qa_analyzer.models import CriminalRecord, Disposition, RuleResult


CONVICTION_DISPOSITIONS = {
    Disposition.CONVICTED,
    Disposition.GUILTY,
    Disposition.NOLO_CONTENDERE,
}

ACTIVE_NON_CONVICTION_DISPOSITIONS = {
    Disposition.PENDING,
    Disposition.DEFERRED,
    Disposition.DIVERSION,
    Disposition.FIRST_OFFENDER,
}

NEVER_REPORTABLE_DISPOSITIONS = {
    Disposition.DISMISSED: "Dismissed",
    Disposition.ACQUITTED: "Acquitted",
    Disposition.NOT_GUILTY: "Not Guilty",
    Disposition.ADJUDICATION_WITHHELD: "Adjudication Withheld (non-conviction)",
    Disposition.DEFERRED_COMPLETED: "Deferred (successfully completed)",
    Disposition.EXPUNGED: "Expunged",
    Disposition.SEALED: "Sealed",
    Disposition.JUVENILE: "Juvenile-court adjudication",
    Disposition.ARREST_ONLY: "Arrest-only (EEOC — not reportable in pre-employment)",
}


def evaluate(record: CriminalRecord) -> RuleResult:
    """Determine if the disposition is reportable."""

    disp = record.disposition

    # Missing disposition → SOP says assume conviction and follow up
    if disp in (Disposition.UNKNOWN, Disposition.NO_DISPOSITION):
        return RuleResult(
            test_id="T3_reportable_disposition",
            test_name="Reportable Disposition",
            passed=False,
            detail=(
                "No disposition available. SOP §2 rule: assume conviction "
                "and follow up with the court/source to obtain final "
                "disposition. Escalate for research team."
            ),
            sop_reference="SOP §2 (Missing Disposition) + §5 test 3, p.11",
            escalate=True,
        )

    if disp in CONVICTION_DISPOSITIONS:
        return RuleResult(
            test_id="T3_reportable_disposition",
            test_name="Reportable Disposition",
            passed=True,
            detail=f"Conviction disposition: '{disp.value}'.",
            sop_reference="SOP §5 test 3, p.11",
        )

    if disp in ACTIVE_NON_CONVICTION_DISPOSITIONS:
        return RuleResult(
            test_id="T3_reportable_disposition",
            test_name="Reportable Disposition",
            passed=True,
            detail=(
                f"Active non-conviction disposition '{disp.value}'. "
                "Reportable per SOP §5 as active pending/deferral, "
                "subject to 7-year rule from arrest (see Test 2)."
            ),
            sop_reference="SOP §5 test 3, p.11 + §7 Federal Rule",
        )

    if disp in NEVER_REPORTABLE_DISPOSITIONS:
        label = NEVER_REPORTABLE_DISPOSITIONS[disp]
        return RuleResult(
            test_id="T3_reportable_disposition",
            test_name="Reportable Disposition",
            passed=False,
            detail=f"Disposition '{label}' is not reportable per SOP.",
            sop_reference="SOP §5 test 3, p.11 + §18",
        )

    # Should not reach here — defensive fallback
    return RuleResult(
        test_id="T3_reportable_disposition",
        test_name="Reportable Disposition",
        passed=False,
        detail=f"Unhandled disposition '{disp.value}'. Escalate for review.",
        sop_reference="SOP §5 test 3, p.11",
        escalate=True,
    )
