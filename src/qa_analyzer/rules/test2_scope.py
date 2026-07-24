"""Test 2 — Is the record within scope of reporting?

SOP §5 test 2, p.11: "FCRA recommendation is 10 years for Misdemeanors
and Felonies indefinitely provided that a state reporting restriction is
not in effect and client does not notate a year/offense restriction."

SOP §6, p.13: Reportability is calculated from the LATEST of:
    - Disposition date
    - Release from incarceration (or max sentence date if no release date)
    - Commencement of parole

Active probation does NOT extend the timeline unless a probation violation
results in incarceration.

For non-convictions (pending/deferred), the 7-year window is calculated
from the ARREST date (per SOP §7 Federal Rule row).
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from qa_analyzer.models import (
    ClientProfile,
    CriminalRecord,
    Disposition,
    OffenseLevel,
    RuleResult,
    Subject,
)
from qa_analyzer.state_law import get_state_rule, state_applies_conviction_cap


# ---------------------------------------------------------------------------
# Constants (SOP §5, §18)
# ---------------------------------------------------------------------------

FCRA_MISDEMEANOR_MAX_YEARS = 10       # SOP §5, §18
FCRA_FELONY_MAX_YEARS = None          # None = indefinite
FCRA_NON_CONVICTION_MAX_YEARS = 7     # SOP §7 Federal Rule


CONVICTION_DISPOSITIONS = {
    Disposition.CONVICTED,
    Disposition.GUILTY,
    Disposition.NOLO_CONTENDERE,
}

NON_CONVICTION_REPORTABLE = {
    Disposition.PENDING,
    Disposition.DEFERRED,
    Disposition.DIVERSION,
    Disposition.FIRST_OFFENDER,
}


# ---------------------------------------------------------------------------
# Controlling-date helper (SOP §6)
# ---------------------------------------------------------------------------


def controlling_date_for(record: CriminalRecord) -> tuple[Optional[date], str]:
    """Compute the controlling date and a human-readable reason.

    For convictions:
        Latest of: disposition_date, release_date (or sentence_max_date),
                   parole_start_date

    For non-convictions (pending/deferred): arrest_date (SOP §7 Federal Rule)

    Note: Active probation does NOT extend the timeline (SOP §6).
    """

    if record.disposition in NON_CONVICTION_REPORTABLE:
        if record.arrest_date:
            return record.arrest_date, "Arrest date (non-conviction 7-yr rule)"
        # No arrest date on a pending → escalate
        return None, "No arrest date on non-conviction record"

    # Convictions: latest of disposition, release/sentence-max, parole
    candidates: list[tuple[date, str]] = []
    if record.disposition_date:
        candidates.append((record.disposition_date, "disposition date"))
    if record.release_date:
        candidates.append((record.release_date, "release date"))
    elif record.sentence_max_date:
        candidates.append((record.sentence_max_date, "max sentence date (no release recorded)"))
    if record.parole_start_date:
        candidates.append((record.parole_start_date, "parole start date"))

    if not candidates:
        return None, "No disposition/release/parole dates available"

    latest = max(candidates, key=lambda x: x[0])
    return latest[0], f"Latest of applicable dates: {latest[1]}"


# ---------------------------------------------------------------------------
# Main evaluator
# ---------------------------------------------------------------------------


def evaluate(
    record: CriminalRecord,
    subject: Subject,
    client: Optional[ClientProfile] = None,
    today: Optional[date] = None,
) -> RuleResult:
    """Determine if the record is within reporting scope."""

    today = today or date.today()
    state_rule = get_state_rule(record.state)
    state_notes: list[str] = []

    # -------------------------------------------------------------------
    # Path A: Non-conviction (pending / deferred / diversion / first offender)
    # -------------------------------------------------------------------
    if record.disposition in NON_CONVICTION_REPORTABLE:
        # State prohibition on pending charges (AK & KY only)
        if state_rule and not state_rule.pending_reportable:
            return RuleResult(
                test_id="T2_within_scope",
                test_name="Within Scope of Reporting",
                passed=False,
                detail=(
                    f"{record.state} does not permit reporting non-conviction "
                    f"records. {'; '.join(state_rule.exclusions)}"
                ),
                sop_reference=state_rule.sop_reference,
            )

        # CA special: deferrals that are completed are not reportable,
        # but they'd already have disposition = DEFERRED_COMPLETED. If
        # currently in progress (DEFERRED), CA allows disclosure.
        # No extra logic needed here — the disposition-completed case
        # is caught by Test 3.

        if not record.arrest_date:
            return RuleResult(
                test_id="T2_within_scope",
                test_name="Within Scope of Reporting",
                passed=False,
                detail=(
                    "Non-conviction record lacks an arrest date. Cannot "
                    "calculate 7-year window. Escalate for human review."
                ),
                sop_reference="SOP §7 Federal Rule + §6",
                escalate=True,
            )

        # State-specific pending windows (AR: 3yr, ID: 1yr, etc.)
        max_years = FCRA_NON_CONVICTION_MAX_YEARS
        source = "FCRA (7 years from arrest)"
        if state_rule and state_rule.pending_max_years is not None \
                and state_rule.pending_max_years < max_years:
            max_years = state_rule.pending_max_years
            source = f"{record.state} rule ({max_years} years from arrest)"
            state_notes.append(source)

        cutoff_years = (today - record.arrest_date).days / 365.25
        if cutoff_years > max_years:
            return RuleResult(
                test_id="T2_within_scope",
                test_name="Within Scope of Reporting",
                passed=False,
                detail=(
                    f"Non-conviction is {cutoff_years:.1f} years old (arrest "
                    f"{record.arrest_date.isoformat()}), beyond {source}."
                ),
                sop_reference="SOP §7 Federal Rule / state table",
            )

        return RuleResult(
            test_id="T2_within_scope",
            test_name="Within Scope of Reporting",
            passed=True,
            detail=(
                f"Non-conviction within {max_years}-year window "
                f"(arrest {record.arrest_date.isoformat()}, "
                f"{cutoff_years:.1f} years ago). Source: {source}."
            ),
            sop_reference="SOP §7 Federal Rule / state table",
        )

    # -------------------------------------------------------------------
    # Path B: Conviction — check state rule, then FCRA fallback
    # -------------------------------------------------------------------

    if record.disposition not in CONVICTION_DISPOSITIONS:
        # Non-reportable disposition — Test 3 will catch it. Skip scope.
        return RuleResult(
            test_id="T2_within_scope",
            test_name="Within Scope of Reporting",
            passed=True,
            detail=(
                f"Disposition '{record.disposition.value}' is not a conviction. "
                "Scope test is not applicable; Test 3 will govern."
            ),
            sop_reference="SOP §5 test 2, p.11",
        )

    ctrl_date, ctrl_reason = controlling_date_for(record)
    if ctrl_date is None:
        return RuleResult(
            test_id="T2_within_scope",
            test_name="Within Scope of Reporting",
            passed=False,
            detail=f"Cannot determine controlling date. {ctrl_reason}",
            sop_reference="SOP §6, p.13",
            escalate=True,
        )

    age_years = (today - ctrl_date).days / 365.25

    # Determine max years — state overrides FCRA where stricter
    is_felony = record.offense_level == OffenseLevel.FELONY
    max_years: Optional[int] = None
    max_source = ""

    # 1) State rule (if applies to this subject's salary)
    if state_rule and state_applies_conviction_cap(state_rule, subject.annual_salary):
        if is_felony and state_rule.conviction_max_years is not None:
            max_years = state_rule.conviction_max_years
            max_source = state_rule.sop_reference
            state_notes.append(f"{record.state} conviction cap: {max_years}y")
        elif not is_felony and state_rule.misdemeanor_max_years is not None:
            max_years = state_rule.misdemeanor_max_years
            max_source = state_rule.sop_reference
            state_notes.append(f"{record.state} misdemeanor cap: {max_years}y")
        elif not is_felony and state_rule.conviction_max_years is not None:
            # State has one general cap that applies to both
            max_years = state_rule.conviction_max_years
            max_source = state_rule.sop_reference
            state_notes.append(f"{record.state} conviction cap: {max_years}y")

    # 2) FCRA fallback (only applies to misdemeanors, felonies indefinite)
    if max_years is None:
        if is_felony:
            max_years = FCRA_FELONY_MAX_YEARS  # None
            max_source = "FCRA (felony indefinite)"
        else:
            max_years = FCRA_MISDEMEANOR_MAX_YEARS
            max_source = "FCRA (misdemeanor 10 years)"

    # 3) Client override (if shorter than what we have)
    if client:
        if is_felony and client.max_years_felony is not None:
            if max_years is None or client.max_years_felony < max_years:
                max_years = client.max_years_felony
                max_source = f"Client override (felony {max_years}y)"
        if not is_felony and client.max_years_misdemeanor is not None:
            if max_years is None or client.max_years_misdemeanor < max_years:
                max_years = client.max_years_misdemeanor
                max_source = f"Client override (misdemeanor {max_years}y)"

    # 4) Salary-cap awareness warning (if salary unknown)
    if state_rule and state_rule.salary_cap is not None and subject.annual_salary is None:
        state_notes.append(
            f"⚠️ {record.state} salary-cap rule (< ${state_rule.salary_cap:,.0f}/yr) "
            "applied conservatively — subject's salary is unknown."
        )

    # Decision
    if max_years is None:
        return RuleResult(
            test_id="T2_within_scope",
            test_name="Within Scope of Reporting",
            passed=True,
            detail=(
                f"Felony conviction, indefinite scope per FCRA. "
                f"Controlling date: {ctrl_date.isoformat()} ({ctrl_reason}), "
                f"{age_years:.1f}y ago. "
                + ("; ".join(state_notes) if state_notes else "")
            ).strip(),
            sop_reference=max_source,
        )

    if age_years <= max_years:
        return RuleResult(
            test_id="T2_within_scope",
            test_name="Within Scope of Reporting",
            passed=True,
            detail=(
                f"Conviction within {max_years}-year window "
                f"({age_years:.1f} years old). Controlling date: "
                f"{ctrl_date.isoformat()} ({ctrl_reason}). Source: {max_source}. "
                + ("; ".join(state_notes) if state_notes else "")
            ).strip(),
            sop_reference=max_source,
        )

    return RuleResult(
        test_id="T2_within_scope",
        test_name="Within Scope of Reporting",
        passed=False,
        detail=(
            f"Conviction is {age_years:.1f} years old, beyond the "
            f"{max_years}-year window. Controlling date: {ctrl_date.isoformat()} "
            f"({ctrl_reason}). Source: {max_source}. "
            + ("; ".join(state_notes) if state_notes else "")
        ).strip(),
        sop_reference=max_source,
    )
