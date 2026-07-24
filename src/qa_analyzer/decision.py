"""Top-level decision engine — combines all four tests into a single verdict.

Applies the sequence from SOP §5:
    1. Test 1 (offense level) — cheapest, fail fast
    2. Test 3 (disposition)   — cheapest, fail fast
    3. Test 2 (scope)         — needs dates & state law
    4. Test 4 (PII match)     — needs full matching evaluation

All four must pass to REPORT. Any test with escalate=True forces
ESCALATE outcome so a human reviews the case.

Also applies cross-cutting rules that don't fit inside a single test:
    - Source confirmation rule (SOP §2): DB-only records must be confirmed
      with the court/source before reporting.
    - Arrest-only records (SOP §5 & §18): never reportable.
    - Illinois special: adjudication withheld/deferred not reportable
      regardless of age IF no other conviction on the report.
    - California marijuana 2-year rule.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from qa_analyzer.matching import matching_summary
from qa_analyzer.models import (
    ClientProfile,
    CriminalRecord,
    Decision,
    DecisionOutcome,
    Disposition,
    OffenseLevel,
    RuleResult,
    Subject,
)
from qa_analyzer.rules.test1_offense import evaluate as test1_offense_evaluate
from qa_analyzer.rules.test2_scope import (
    evaluate as test2_scope_evaluate,
    controlling_date_for,
)
from qa_analyzer.rules.test3_disposition import evaluate as test3_disposition_evaluate
from qa_analyzer.rules.test4_pii import evaluate as test4_pii_evaluate
from qa_analyzer.state_law import get_state_rule


def analyze_record(
    record: CriminalRecord,
    subject: Subject,
    client: Optional[ClientProfile] = None,
    today: Optional[date] = None,
    other_records_on_report: Optional[list[CriminalRecord]] = None,
) -> Decision:
    """Run all four tests + cross-cutting rules against a single record.

    Args:
        record: The criminal record to evaluate.
        subject: The subject being background-checked.
        client: Optional client profile with restrictions.
        today: Date to evaluate against (defaults to today).
        other_records_on_report: For IL special rule — the other records
            that would appear on the same consumer report.

    Returns:
        A Decision with outcome and full audit trail.
    """
    today = today or date.today()
    other_records_on_report = other_records_on_report or []

    decision = Decision(
        record_id=record.record_id,
        outcome=DecisionOutcome.EXCLUDE,  # start pessimistic
    )

    # -------------------------------------------------------------------
    # Cross-cutting pre-checks (short-circuit)
    # -------------------------------------------------------------------

    # 1. Arrest-only records are NEVER reportable (EEOC, SOP §18)
    if record.disposition == Disposition.ARREST_ONLY:
        r = RuleResult(
            test_id="PRE_arrest_only",
            test_name="Arrest-Only Record",
            passed=False,
            detail=(
                "Arrest-only records are never reportable in pre-employment "
                "per EEOC. Must locate officially filed charges under a "
                "case number before any reporting."
            ),
            sop_reference="SOP §5 & §18 (EEOC)",
        )
        decision.rule_results.append(r)
        decision.outcome = DecisionOutcome.EXCLUDE
        return decision

    # 2. Source-confirmation rule (SOP §2, p.3)
    #    Records must be confirmed with the court/authoritative source
    #    before being reported. Database-only hits are pointers.
    if not record.source_confirmed:
        decision.warnings.append(
            f"Record source '{record.source}' is not marked source-confirmed. "
            "Per SOP §2: 'NO RECORDS SHOULD BE REPORTED DIRECTLY FROM THE "
            "DATABASE — ALWAYS CONFIRM WITH THE SOURCE.'"
        )
        # This is a warning, not an auto-exclude — the operator may have
        # verified but not flagged. Escalate at the end if not overridden.

    # -------------------------------------------------------------------
    # Test 1 — Reportable offense level
    # -------------------------------------------------------------------
    r1 = test1_offense_evaluate(record)
    decision.rule_results.append(r1)

    # -------------------------------------------------------------------
    # Test 3 — Reportable disposition (checked before scope; cheap)
    # -------------------------------------------------------------------
    r3 = test3_disposition_evaluate(record)
    decision.rule_results.append(r3)

    # -------------------------------------------------------------------
    # Test 2 — Within reporting scope (state + FCRA + client)
    # -------------------------------------------------------------------
    r2 = test2_scope_evaluate(record, subject, client, today)
    decision.rule_results.append(r2)

    # Capture controlling date for output
    ctrl_date, ctrl_reason = controlling_date_for(record)
    decision.controlling_date = ctrl_date
    decision.controlling_date_reason = ctrl_reason

    # -------------------------------------------------------------------
    # Test 4 — Sufficient PII to match
    # -------------------------------------------------------------------
    r4 = test4_pii_evaluate(record, subject)
    decision.rule_results.append(r4)

    # Capture matching summary
    decision.matching_score = matching_summary(record, subject)

    # -------------------------------------------------------------------
    # Cross-cutting post-checks
    # -------------------------------------------------------------------

    # State-specific overlays (record state limitations applied)
    state_rule = get_state_rule(record.state)
    if state_rule:
        decision.state_rules_applied.extend(state_rule.exclusions)

    # California marijuana 2-year rule
    if (
        record.state.upper() == "CA"
        and record.is_marijuana_possession
        and record.offense_level != OffenseLevel.FELONY
    ):
        if ctrl_date:
            age_yrs = (today - ctrl_date).days / 365.25
            if age_yrs > 2:
                decision.rule_results.append(RuleResult(
                    test_id="POST_ca_marijuana",
                    test_name="CA Marijuana 2-Year Rule",
                    passed=False,
                    detail=(
                        f"California non-felony marijuana possession is "
                        f"{age_yrs:.1f} years old, beyond the 2-year "
                        "reporting limit."
                    ),
                    sop_reference="SOP §7 p.14 (California)",
                ))

    # Illinois adjudication-withheld/deferred rule
    if (
        record.state.upper() == "IL"
        and record.disposition in (
            Disposition.ADJUDICATION_WITHHELD,
            Disposition.DEFERRED,
            Disposition.DEFERRED_COMPLETED,
        )
    ):
        has_other_convictions = any(
            r.disposition in (
                Disposition.CONVICTED,
                Disposition.GUILTY,
                Disposition.NOLO_CONTENDERE,
            )
            for r in other_records_on_report
            if r.record_id != record.record_id
        )
        if not has_other_convictions:
            decision.rule_results.append(RuleResult(
                test_id="POST_illinois_adj_withheld",
                test_name="Illinois Adjudication-Withheld Rule",
                passed=False,
                detail=(
                    "Illinois: adjudication withheld / deferred not "
                    "reportable regardless of age IF no other conviction "
                    "on the report."
                ),
                sop_reference="SOP §7 p.14 (Illinois)",
            ))

    # -------------------------------------------------------------------
    # Final outcome
    # -------------------------------------------------------------------
    all_results: list[RuleResult] = decision.rule_results

    # Precedence:
    #   1. Any hard-fail (passed=False, escalate=False) → EXCLUDE
    #      (record is definitively not reportable; identity ambiguity moot)
    #   2. Any escalate (passed=False, escalate=True) → ESCALATE
    #      (need human input to complete evaluation)
    #   3. All passed → REPORT (unless source unconfirmed → ESCALATE)
    any_hard_fail = any(not r.passed and not r.escalate for r in all_results)
    any_escalate = any(r.escalate for r in all_results)

    if any_hard_fail:
        decision.outcome = DecisionOutcome.EXCLUDE
        # Still surface escalate reasons for the record so the operator
        # sees what else was uncertain.
        decision.escalation_reasons = [
            r.detail for r in all_results if r.escalate
        ]
    elif any_escalate:
        decision.outcome = DecisionOutcome.ESCALATE
        decision.escalation_reasons = [
            r.detail for r in all_results if r.escalate
        ]
    else:
        # All passed — but source-confirmed check is a soft escalate
        if not record.source_confirmed:
            decision.outcome = DecisionOutcome.ESCALATE
            decision.escalation_reasons.append(
                "Source not confirmed. Confirm with court/authoritative "
                "source before reporting (SOP §2)."
            )
        else:
            decision.outcome = DecisionOutcome.REPORT

    return decision
