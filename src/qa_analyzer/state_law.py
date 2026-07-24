"""State reporting limitations table.

Source: VICTIG SOP §7 "Limitations Beyond the FCRA for Criminal Records"
(pages 14-15) cross-referenced with §18 (page 18).

Every entry cites its SOP source. When Kate updates the SOP, only this
file needs to change to keep the engine current.

SOP ambiguities resolved with Matt (2026-07-24):
   - Mississippi: confirmed NOT a strict 7-year state (p.18 mention was
     erroneous). Removed from the table — falls back to FCRA default.
   - NameGrade threshold: use 58 (per operational SOP §5 test 4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StateRule:
    """Per-state reporting limitations."""

    state: str                                    # 2-letter code

    # Conviction reporting
    conviction_max_years: Optional[int] = None    # None = indefinite (FCRA default)
    # Special: for Hawaii, misdemeanor scope differs from felony
    misdemeanor_max_years: Optional[int] = None   # None = follows conviction rule

    # Pending / active non-conviction reporting
    # Only AK & KY prohibit pending charges entirely ("conviction only" states)
    # NY & CA phrase "non-convictions NOT reportable" but that refers to
    # dispositions like dismissed/acquitted (already excluded by Test 3);
    # both states explicitly permit reporting PENDING charges.
    pending_reportable: bool = True               # False = conviction-only state (AK, KY)
    pending_max_years: Optional[int] = 7          # FCRA baseline
    pending_from_arrest: bool = True              # calc from arrest date, not disp

    # Deferrals/diversions
    deferrals_reportable_if_completed: bool = True  # False for CA

    # Salary condition — restrictions only apply if salary is BELOW threshold
    salary_cap: Optional[float] = None            # None = restriction always applies

    # Calculation method
    exclude_incarceration_time: bool = False      # HI: use disposition-only
    calc_from_release_or_disposition: bool = True # standard: latest of disp/release/parole

    # Special exclusions & notes
    exclusions: list[str] = field(default_factory=list)  # human-readable notes
    sop_reference: str = ""


# ---------------------------------------------------------------------------
# State table — SOP §7 (pages 14-15) + §18 (page 18)
# ---------------------------------------------------------------------------

_STATE_RULES: dict[str, StateRule] = {
    "AK": StateRule(
        state="AK",
        pending_reportable=False,
        pending_max_years=None,   # not reportable regardless of age
        exclusions=[
            "Convictions only. Non-convictions, pending, and rehabilitation "
            "are NOT reportable regardless of age.",
        ],
        sop_reference="SOP §7 p.14 (Alaska, yellow)",
    ),
    "AR": StateRule(
        state="AR",
        pending_max_years=3,
        exclusions=[
            "Pending reportable 3 years from arrest date.",
            "Non-conviction felony charges reportable 3 years from arrest.",
        ],
        sop_reference="SOP §7 p.14 (Arkansas)",
    ),
    "CA": StateRule(
        state="CA",
        conviction_max_years=7,
        misdemeanor_max_years=7,
        pending_reportable=True,      # SOP: "Pending reportable 7 yrs from arrest"
        pending_max_years=7,
        pending_from_arrest=True,
        deferrals_reportable_if_completed=False,
        exclusions=[
            "Non-convictions NOT reportable.",
            "Deferrals NOT reportable if successfully completed "
            "(may disclose if diversion currently in progress).",
            "DO NOT REPORT non-felony marijuana possession older than 2 years.",
        ],
        sop_reference="SOP §7 p.14 (California)",
    ),
    "DC": StateRule(
        state="DC",
        conviction_max_years=10,
        exclusions=[
            "Convictions reportable 10 years from completing sentence.",
        ],
        sop_reference="SOP §7 p.14 (District of Columbia)",
    ),
    "HI": StateRule(
        state="HI",
        conviction_max_years=7,       # felonies: 7yr from disposition only
        misdemeanor_max_years=5,      # misdemeanors: 5yr from disposition only
        exclude_incarceration_time=True,
        exclusions=[
            "Felony convictions reportable 7 years from DISPOSITION ONLY "
            "(incarceration time NOT counted).",
            "Misdemeanor convictions reportable 5 years from DISPOSITION ONLY.",
        ],
        sop_reference="SOP §7 p.14 (Hawaii)",
    ),
    "ID": StateRule(
        state="ID",
        pending_max_years=1,
        exclusions=[
            "Pending reportable 1 year from arrest unless written "
            "permission from consumer.",
        ],
        sop_reference="SOP §7 p.14 (Idaho)",
    ),
    "IL": StateRule(
        state="IL",
        exclusions=[
            "DO NOT REPORT ADJUDICATION WITHHELD OR DEFERRED CHARGES "
            "regardless of age IF there is no other conviction on the report.",
        ],
        sop_reference="SOP §7 p.14 (Illinois)",
    ),
    "IN": StateRule(
        state="IN",
        exclusions=[
            "Do not report Class D or Level 6 Felony if amended to a "
            "Class A Misdemeanor (the misdemeanor CAN be reported).",
        ],
        sop_reference="SOP §7 p.14 (Indiana)",
    ),
    "KS": StateRule(
        state="KS",
        conviction_max_years=7,
        salary_cap=20000.0,
        exclusions=[
            "Convictions reportable 7 years IF consumer earns < $20k/yr.",
        ],
        sop_reference="SOP §7 p.14 (Kansas, salary-cap)",
    ),
    "KY": StateRule(
        state="KY",
        pending_reportable=False,
        pending_max_years=None,
        exclusions=[
            "Convictions only. Non-convictions and pending NOT reportable.",
        ],
        sop_reference="SOP §7 p.14 (Kentucky, yellow)",
    ),
    "LA": StateRule(
        state="LA",
        exclusions=[
            "Traffic violations for failure to pay NOT reportable until "
            "120 days past final disposition.",
        ],
        sop_reference="SOP §7 p.15 (Louisiana)",
    ),
    "MD": StateRule(
        state="MD",
        conviction_max_years=7,
        salary_cap=75000.0,
        exclusions=[
            "Convictions reportable 7 years from disposition/release/parole "
            "IF consumer earns < $75k/yr.",
        ],
        sop_reference="SOP §7 p.15 (Maryland, salary-cap)",
    ),
    "MA": StateRule(
        state="MA",
        conviction_max_years=7,
        misdemeanor_max_years=3,      # MA Ban-The-Box
        exclusions=[
            "Felony convictions reportable 7 years from disposition/release/parole.",
            "Misdemeanor convictions reportable only 3 years (Ban The Box).",
        ],
        sop_reference="SOP §7 p.15 (Massachusetts) + §18 (Ban The Box)",
    ),
    # MS — confirmed NOT a strict 7-year state (Matt, 2026-07-24).
    # Falls back to FCRA default (10yr misdemeanor, indefinite felony).
    "MT": StateRule(
        state="MT",
        conviction_max_years=7,
        exclusions=[
            "Convictions reportable 7 years from disposition/release/parole.",
        ],
        sop_reference="SOP §7 p.15 (Montana)",
    ),
    "NE": StateRule(
        state="NE",
        exclusions=[
            "No access to arrest information.",
        ],
        sop_reference="SOP §7 p.15 (Nebraska)",
    ),
    "NH": StateRule(
        state="NH",
        conviction_max_years=7,
        salary_cap=20000.0,
        exclusions=[
            "Convictions reportable 7 years from disposition/release/parole "
            "IF consumer earns < $20k/yr.",
        ],
        sop_reference="SOP §7 p.15 (New Hampshire, salary-cap)",
    ),
    "NM": StateRule(
        state="NM",
        conviction_max_years=7,
        exclusions=[
            "Convictions reportable 7 years from disposition/release/parole.",
        ],
        sop_reference="SOP §7 p.15 (New Mexico)",
    ),
    "NY": StateRule(
        state="NY",
        conviction_max_years=7,
        salary_cap=25000.0,
        pending_reportable=True,      # SOP: "Pending ARE reportable."
        exclusions=[
            "Convictions reportable 7 years IF consumer earns < $25k/yr.",
            "Pending ARE reportable.",
            "Non-convictions NOT reportable regardless of income (already "
            "handled by Test 3 for dismissed/acquitted dispositions).",
        ],
        sop_reference="SOP §7 p.15 (New York, salary-cap)",
    ),
    "WA": StateRule(
        state="WA",
        conviction_max_years=7,
        salary_cap=20000.0,
        exclusions=[
            "Convictions reportable 7 years from disposition/release/parole "
            "IF consumer earns < $20k/yr.",
        ],
        sop_reference="SOP §7 p.15 (Washington, salary-cap)",
    ),
}


def get_state_rule(state_code: str) -> Optional[StateRule]:
    """Return the state's rule, or None if no restrictions beyond FCRA."""
    if not state_code:
        return None
    return _STATE_RULES.get(state_code.upper())


def state_applies_conviction_cap(rule: StateRule, subject_salary: Optional[float]) -> bool:
    """Determine if a state's conviction cap applies to this subject.

    Salary-cap states only restrict reporting if the subject earns below
    the threshold. If salary is unknown, we conservatively assume the cap
    applies (worst case: exclude a record that could have been reported —
    fails safe on the FCRA side).
    """
    if rule.salary_cap is None:
        # Restriction always applies (not salary-conditional)
        return True
    if subject_salary is None:
        # Unknown salary: assume cap applies (conservative)
        return True
    return subject_salary < rule.salary_cap


def all_states() -> list[str]:
    """Return all state codes with special rules."""
    return sorted(_STATE_RULES.keys())
