"""Data models for the VICTIG QA Analyzer.

Uses dataclasses (stdlib) so this can run anywhere without pip installs.
Field docs reference the VICTIG SOP section where the concept originates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class OffenseLevel(str, Enum):
    """SOP §5 test 1 + §18 reporting guidelines.

    Only FELONY and MISDEMEANOR (or state equivalents) are reportable.
    All other levels are non-reportable "petty" offenses.
    """

    FELONY = "felony"
    MISDEMEANOR = "misdemeanor"
    # Non-reportable levels (SOP §18):
    PETTY_MISDEMEANOR = "petty_misdemeanor"      # MN
    MINOR_MISDEMEANOR = "minor_misdemeanor"      # OH
    TRAFFIC_INFRACTION = "traffic_infraction"
    ORDINANCE = "ordinance"                       # noise/dogs/littering
    FORFEITURE_U = "forfeiture_u"                 # WI
    SUMMARY_OFFENSE = "summary_offense"           # PA
    PETTY_DISORDERLY = "petty_disorderly"         # NJ Petty Disorderly Persons
    UNKNOWN = "unknown"


class Disposition(str, Enum):
    """SOP §5 test 3.

    Convictions are reportable; certain non-convictions (pending/deferred)
    are reportable for 7 years from arrest; the rest are excluded.
    """

    # Reportable (convictions):
    CONVICTED = "convicted"
    GUILTY = "guilty"
    NOLO_CONTENDERE = "nolo_contendere"           # treated as conviction

    # Reportable as active non-convictions (with 7-yr rule):
    PENDING = "pending"
    DEFERRED = "deferred"                          # active/in-progress
    DIVERSION = "diversion"                        # active/in-progress
    FIRST_OFFENDER = "first_offender"              # active/in-progress

    # Non-reportable (excluded regardless of age, per SOP):
    DISMISSED = "dismissed"
    ACQUITTED = "acquitted"
    NOT_GUILTY = "not_guilty"
    ADJUDICATION_WITHHELD = "adjudication_withheld"
    DEFERRED_COMPLETED = "deferred_completed"      # successfully completed
    EXPUNGED = "expunged"
    SEALED = "sealed"
    JUVENILE = "juvenile"
    ARREST_ONLY = "arrest_only"                    # never reportable (EEOC)
    NO_DISPOSITION = "no_disposition"              # SOP: assume conviction & follow up
    UNKNOWN = "unknown"


class Gender(str, Enum):
    MALE = "male"
    FEMALE = "female"
    UNKNOWN = "unknown"


class DecisionOutcome(str, Enum):
    REPORT = "REPORT"                   # all 4 tests passed
    EXCLUDE = "EXCLUDE"                 # at least one test failed cleanly
    ESCALATE = "ESCALATE"               # ambiguous / needs human review


# ---------------------------------------------------------------------------
# Input models
# ---------------------------------------------------------------------------


@dataclass
class Subject:
    """The person being background-checked (SOP §1 & §8)."""

    first_name: str
    last_name: str
    middle_name: Optional[str] = None
    dob: Optional[date] = None
    ssn_last4: Optional[str] = None
    gender: Optional[Gender] = None
    race: Optional[str] = None
    # Address history — list of (state, county, from_date, to_date)
    address_history: list[dict] = field(default_factory=list)
    # Salary info for state salary-cap rules (KS/MD/NH/NY/WA)
    annual_salary: Optional[float] = None
    # NameGrade™ score from VICTIG's algorithm (0-100+, higher = more common)
    name_grade: Optional[int] = None


@dataclass
class CriminalRecord:
    """A single record retrieved from a court or database (SOP §2 & §6)."""

    record_id: str

    # Source of record (for the "confirm with source" rule in §2)
    source: str                                   # e.g. "NetPlus", "County Court", "PACER"
    source_confirmed: bool = False                # True if court/authoritative

    # Charge details
    charge_description: str = ""
    offense_level: OffenseLevel = OffenseLevel.UNKNOWN
    disposition: Disposition = Disposition.UNKNOWN

    # Dates (SOP §6: reportability is calculated from the LATEST of these)
    offense_date: Optional[date] = None
    arrest_date: Optional[date] = None
    file_date: Optional[date] = None              # standard: "court case filing date"
    disposition_date: Optional[date] = None
    release_date: Optional[date] = None
    parole_start_date: Optional[date] = None
    sentence_max_date: Optional[date] = None      # if no release, use max sentence

    # Jurisdiction
    state: str = ""                                # 2-letter state code
    county: str = ""
    court_name: str = ""
    case_number: str = ""

    # Record-holder identifiers (for matching, SOP §8)
    record_first_name: str = ""
    record_last_name: str = ""
    record_middle_name: Optional[str] = None
    record_dob: Optional[date] = None
    record_ssn_last4: Optional[str] = None
    record_gender: Optional[Gender] = None
    record_race: Optional[str] = None
    record_address_state: Optional[str] = None

    # Special flags
    is_marijuana_possession: bool = False          # for CA 2-yr rule
    is_amended_from_felony: bool = False           # for IN Class D/Level 6 rule
    is_probation_violation_incarceration: bool = False  # SOP §6 note


@dataclass
class ClientProfile:
    """Client-imposed restrictions (SOP §5 test 2, §18)."""

    client_id: str = ""
    client_name: str = ""
    # Standard 10-year scope may be shortened or extended in writing
    max_years_misdemeanor: Optional[int] = None    # None = FCRA default (10)
    max_years_felony: Optional[int] = None         # None = indefinite per FCRA
    # Some clients only want felonies, or only sex offenses, etc.
    felonies_only: bool = False
    sex_offenses_only: bool = False
    # Custom offense-type restrictions (list of substrings to include/exclude)
    include_offense_keywords: list[str] = field(default_factory=list)
    exclude_offense_keywords: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


@dataclass
class RuleResult:
    """Result of one rule/test with an audit trail."""

    test_id: str                                  # e.g. "T1_reportable_offense"
    test_name: str
    passed: bool
    detail: str                                    # human-readable explanation
    sop_reference: str = ""                        # e.g. "SOP §5 test 1, p.11"
    escalate: bool = False                         # if True, force human review


@dataclass
class Decision:
    """Final reportability decision with full audit trail."""

    record_id: str
    outcome: DecisionOutcome
    rule_results: list[RuleResult] = field(default_factory=list)
    matching_score: dict = field(default_factory=dict)
    state_rules_applied: list[str] = field(default_factory=list)
    controlling_date: Optional[date] = None
    controlling_date_reason: str = ""
    warnings: list[str] = field(default_factory=list)
    escalation_reasons: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """One-line summary for logs/CLI output."""
        return f"[{self.outcome.value}] {self.record_id}"

    def to_dict(self) -> dict:
        """Serialize for JSON output."""
        return {
            "record_id": self.record_id,
            "outcome": self.outcome.value,
            "rule_results": [
                {
                    "test_id": r.test_id,
                    "test_name": r.test_name,
                    "passed": r.passed,
                    "detail": r.detail,
                    "sop_reference": r.sop_reference,
                    "escalate": r.escalate,
                }
                for r in self.rule_results
            ],
            "matching_score": self.matching_score,
            "state_rules_applied": self.state_rules_applied,
            "controlling_date": (
                self.controlling_date.isoformat() if self.controlling_date else None
            ),
            "controlling_date_reason": self.controlling_date_reason,
            "warnings": self.warnings,
            "escalation_reasons": self.escalation_reasons,
        }
