"""VICTIG Matching Policy — Levels 1, 2, 3 (SOP §8, pp.16-17).

"Prove it or remove it." A record cannot be reported unless it can be
tied to the subject with confidence.

Level One (identifies a specific individual):
    - Full Name
    - Date of Birth
    - SSN (or partial) — the only truly unique identifier
    - Middle Name / Middle Initial

Level Two (disqualifies a match if it doesn't match):
    - Gender
    - Race
    - Physical Description (not modeled — usually unavailable in structured data)
    - Partial Date of Birth (not modeled here; treated as a DOB match with tolerance)

Level Three (red flags → increased scrutiny, not auto-exclude):
    - Address/State doesn't match any former residence
    - Middle initial/suffix doesn't match
    - Consumer has a common name

Rules from §8:
    - Match ≥ 2 of 4 Level One criteria
    - If any Level Two criterion is present but doesn't match → EXCLUDE
    - Level Three flags require review; common-name + no locational match
      needs approval from ≥ 2 supervisors

NameGrade™ threshold (SOP §5 test 4, p.11 vs. §8 p.17):
    - Operational SOP (p.11): "58 or above" → COMMON name → 3 identifiers
    - Algorithm text (p.17): "above 56"
    Configurable via NAMEGRADE_THRESHOLD below.
"""

from __future__ import annotations

from typing import Optional

from qa_analyzer.models import CriminalRecord, Gender, Subject


# Set to 58 per operational SOP §5 test 4, p.11.
# Kate should confirm — p.17 references 56 for the algorithm.
NAMEGRADE_THRESHOLD = 58


def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()


def _matches(a: Optional[str], b: Optional[str]) -> bool:
    return bool(_norm(a)) and _norm(a) == _norm(b)


# ---------------------------------------------------------------------------
# Level One — identifiers
# ---------------------------------------------------------------------------


def level_one_matches(record: CriminalRecord, subject: Subject) -> dict[str, Optional[bool]]:
    """Return match status for each Level 1 criterion.

    Values:
        True  = matches
        False = does not match
        None  = data missing on one or both sides (can't compare)
    """

    # Full name = first + last
    if not record.record_first_name or not record.record_last_name:
        full_name_match: Optional[bool] = None
    else:
        full_name_match = _matches(subject.first_name, record.record_first_name) and \
                          _matches(subject.last_name, record.record_last_name)

    # DOB
    if subject.dob is None or record.record_dob is None:
        dob_match: Optional[bool] = None
    else:
        dob_match = subject.dob == record.record_dob

    # SSN (last 4)
    if not subject.ssn_last4 or not record.record_ssn_last4:
        ssn_match: Optional[bool] = None
    else:
        ssn_match = _norm(subject.ssn_last4) == _norm(record.record_ssn_last4)

    # Middle name/initial
    if not subject.middle_name or not record.record_middle_name:
        middle_match: Optional[bool] = None
    else:
        # Match on initial if either side is single-char
        s = _norm(subject.middle_name)
        r = _norm(record.record_middle_name)
        if len(s) == 1 or len(r) == 1:
            middle_match = s[0] == r[0]
        else:
            middle_match = s == r

    return {
        "full_name": full_name_match,
        "dob": dob_match,
        "ssn": ssn_match,
        "middle_name": middle_match,
    }


# ---------------------------------------------------------------------------
# Level Two — disqualifiers
# ---------------------------------------------------------------------------


def _has_gender(g) -> bool:
    """Return True only if gender is present AND not UNKNOWN."""
    return g is not None and g != Gender.UNKNOWN


def level_two_disqualifiers(record: CriminalRecord, subject: Subject) -> list[str]:
    """Return descriptions of any Level 2 disqualifiers.

    Only counts a criterion if BOTH sides have data AND they disagree.
    Missing data on either side is not a disqualifier (per SOP: "if any
    is available but does not match").
    """
    disqualifiers = []

    if (
        _has_gender(subject.gender)
        and _has_gender(record.record_gender)
        and subject.gender != record.record_gender
    ):
        disqualifiers.append(
            f"Gender mismatch (subject: {subject.gender.value}, "
            f"record: {record.record_gender.value})"
        )

    if subject.race and record.record_race:
        if _norm(subject.race) != _norm(record.record_race):
            disqualifiers.append(
                f"Race mismatch (subject: {subject.race}, "
                f"record: {record.record_race})"
            )

    return disqualifiers


# ---------------------------------------------------------------------------
# Level Three — red flags
# ---------------------------------------------------------------------------


def level_three_flags(record: CriminalRecord, subject: Subject) -> list[str]:
    """Return descriptions of any Level 3 red flags (require scrutiny)."""
    flags = []

    # Address/state doesn't match any former residence
    if record.record_address_state and subject.address_history:
        subj_states = {
            _norm(a.get("state")) for a in subject.address_history if a.get("state")
        }
        if _norm(record.record_address_state) not in subj_states:
            flags.append(
                f"Record location ({record.record_address_state}) does not "
                f"appear in subject's address history "
                f"({', '.join(sorted(subj_states)) or 'unknown'})"
            )
    # Record has an address state but subject has no address history at all
    elif record.record_address_state and not subject.address_history:
        flags.append(
            f"Subject has no address history to compare against record "
            f"location ({record.record_address_state})"
        )

    # Middle initial/suffix doesn't match (only if both present)
    if subject.middle_name and record.record_middle_name:
        s = _norm(subject.middle_name)
        r = _norm(record.record_middle_name)
        # Handle initial-vs-full comparison
        if len(s) == 1 or len(r) == 1:
            if s[0] != r[0]:
                flags.append(f"Middle initial mismatch ({s[0]} vs {r[0]})")
        elif s != r:
            flags.append(f"Middle name mismatch ({s} vs {r})")

    # Common name (NameGrade above threshold)
    if subject.name_grade is not None and subject.name_grade >= NAMEGRADE_THRESHOLD:
        flags.append(
            f"Common name (NameGrade={subject.name_grade} ≥ {NAMEGRADE_THRESHOLD})"
        )

    return flags


# ---------------------------------------------------------------------------
# Aggregate summary for the Decision output
# ---------------------------------------------------------------------------


def matching_summary(record: CriminalRecord, subject: Subject) -> dict:
    """Structured summary of the matching-policy evaluation."""
    l1 = level_one_matches(record, subject)
    l2 = level_two_disqualifiers(record, subject)
    l3 = level_three_flags(record, subject)
    match_count = sum(1 for v in l1.values() if v is True)
    return {
        "level_one_matches": {k: v for k, v in l1.items()},
        "level_one_match_count": match_count,
        "level_two_disqualifiers": l2,
        "level_three_flags": l3,
        "name_grade": subject.name_grade,
        "namegrade_threshold": NAMEGRADE_THRESHOLD,
        "common_name": (
            subject.name_grade is not None
            and subject.name_grade >= NAMEGRADE_THRESHOLD
        ),
    }
