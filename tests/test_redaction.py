"""Unit tests for the PII redaction pipeline.

The invariant we test: for a well-formed paste, redact() removes real
PII and PIIMap.substitute_string() puts it back exactly.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

import pytest  # noqa: E402

from qa_analyzer.redaction import PIIMap, redact, round_trip_check  # noqa: E402


# ---------------------------------------------------------------------------
# Labeled names
# ---------------------------------------------------------------------------


def test_labeled_full_name_is_redacted():
    text = "Name: John Robert Smith\nDOB: 06/15/1985"
    redacted, pmap = redact(text)
    assert "John" not in redacted
    assert "Robert" not in redacted
    assert "Smith" not in redacted
    stats = pmap.stats()
    assert stats.get("name") == 1
    assert stats.get("dob") == 1


def test_defendant_label_variant():
    text = "Defendant Name: Jane Marie Doe"
    redacted, pmap = redact(text)
    assert "Jane" not in redacted
    assert "Doe" not in redacted
    assert pmap.stats().get("name") == 1


def test_two_distinct_subjects_get_distinct_pseudonyms():
    text = (
        "Candidate: John Smith\n"
        "Co-defendant: Mary Johnson\n"
    )
    redacted, pmap = redact(text)
    names = [e for e in pmap.entities if e.kind == "name"]
    assert len(names) == 2
    assert names[0].fake != names[1].fake


def test_same_name_appearing_twice_collapses_to_one_pseudonym():
    text = (
        "Name: John Robert Smith\n"
        "Defendant Name: John R Smith\n"
        "John Smith was arrested for theft."
    )
    redacted, pmap = redact(text)
    names = [e for e in pmap.entities if e.kind == "name"]
    assert len(names) == 1  # collapsed by fuzzy match


def test_inverted_name_format_is_redacted():
    text = "Case caption: SMITH, JOHN A vs. State of Utah"
    redacted, pmap = redact(text)
    assert "SMITH" not in redacted
    assert "JOHN" not in redacted
    assert pmap.stats().get("name") == 1


# ---------------------------------------------------------------------------
# DOB
# ---------------------------------------------------------------------------


def test_dob_is_redacted_when_labeled():
    text = "DOB: 06/15/1985"
    redacted, pmap = redact(text)
    assert "06/15/1985" not in redacted
    assert "1985" not in redacted
    assert pmap.stats().get("dob") == 1


def test_non_dob_dates_are_preserved():
    """Arrest dates, disposition dates etc should NOT be redacted."""
    text = "Arrest Date: 05/10/2020\nDisposition Date: 11/20/2020"
    redacted, pmap = redact(text)
    assert "05/10/2020" in redacted
    assert "11/20/2020" in redacted
    assert pmap.stats().get("dob", 0) == 0


def test_dob_multiple_formats():
    formats = [
        "DOB: 1985-06-15",
        "Date of Birth: 06/15/1985",
        "D.O.B. 6/15/85",
        "Born: January 5, 1985",
    ]
    for text in formats:
        redacted, pmap = redact(text)
        assert pmap.stats().get("dob") == 1, f"failed on: {text}"


# ---------------------------------------------------------------------------
# SSN
# ---------------------------------------------------------------------------


def test_full_ssn_is_redacted():
    text = "SSN: 123-45-6789"
    redacted, pmap = redact(text)
    assert "123-45-6789" not in redacted
    assert pmap.stats().get("ssn") == 1


def test_masked_ssn_last4_is_redacted():
    text = "SSN: XXX-XX-1234"
    redacted, pmap = redact(text)
    assert "1234" not in redacted
    assert pmap.stats().get("ssn") == 1


def test_ssn_no_dashes():
    text = "SSN 123456789"
    redacted, pmap = redact(text)
    assert "123456789" not in redacted
    assert pmap.stats().get("ssn") == 1


# ---------------------------------------------------------------------------
# Address / phone / email
# ---------------------------------------------------------------------------


def test_street_address_is_redacted():
    text = "Address: 123 Main St, Salt Lake City, UT 84101"
    redacted, pmap = redact(text)
    assert "123 Main St" not in redacted
    # City/state kept — jurisdictional
    assert "Salt Lake City" in redacted
    assert "UT" in redacted
    assert pmap.stats().get("address") == 1


def test_phone_is_redacted():
    text = "Phone: (801) 555-1234\nAlso: 801.555.1234"
    redacted, pmap = redact(text)
    assert "555-1234" not in redacted
    assert "555.1234" not in redacted
    assert pmap.stats().get("phone") >= 1


def test_email_is_redacted():
    text = "Contact: john.smith@example.com"
    redacted, pmap = redact(text)
    assert "john.smith@example.com" not in redacted
    assert pmap.stats().get("email") == 1


# ---------------------------------------------------------------------------
# Case numbers
# ---------------------------------------------------------------------------


def test_case_number_is_redacted():
    text = "Case Number: 20-1234-CR"
    redacted, pmap = redact(text)
    assert "20-1234-CR" not in redacted
    assert pmap.stats().get("case") == 1


def test_docket_number_alt_label():
    text = "Docket No.: 2024CR001234"
    redacted, pmap = redact(text)
    assert "2024CR001234" not in redacted
    assert pmap.stats().get("case") == 1


# ---------------------------------------------------------------------------
# Structural preservation
# ---------------------------------------------------------------------------


def test_charge_disposition_court_preserved():
    text = (
        "Name: John Smith\n"
        "Charge: Grand Theft (Felony)\n"
        "Court: 3rd District Court, Salt Lake County, UT\n"
        "Disposition: Convicted\n"
        "Arrest Date: 05/10/2020\n"
    )
    redacted, _ = redact(text)
    # These must survive redaction \u2014 the parser needs them
    assert "Grand Theft" in redacted
    assert "Felony" in redacted
    assert "3rd District Court" in redacted
    assert "Salt Lake County" in redacted
    assert "UT" in redacted
    assert "Convicted" in redacted
    assert "05/10/2020" in redacted


def test_state_is_preserved():
    text = "Address: 123 Main St, Salt Lake City, UT 84101"
    redacted, _ = redact(text)
    assert "UT" in redacted


# ---------------------------------------------------------------------------
# Round-trip substitution
# ---------------------------------------------------------------------------


def test_round_trip_restores_all_pii():
    """After redact + substitute_string, the restored text should
    contain all the real PII values (though DOB may be ISO-normalized).
    """
    text = (
        "CANDIDATE:\n"
        "Name: John Robert Smith\n"
        "DOB: 06/15/1985\n"
        "SSN: XXX-XX-1234\n"
        "Address: 123 Main St, Salt Lake City, UT 84101\n"
        "Phone: 801-555-1234\n"
        "Email: john@example.com\n"
        "\n"
        "CRIMINAL RECORD:\n"
        "Case Number: 201234567\n"
        "Charge: Grand Theft (Felony)\n"
        "Court: 3rd District Court, Salt Lake County, UT\n"
        "Disposition: Convicted\n"
        "Arrest Date: 05/10/2020\n"
    )
    redacted, pmap = redact(text)
    restored = pmap.substitute_string(redacted)

    # Names, SSN, address, phone, email, case# must all reappear.
    assert "John Robert Smith" in restored
    assert "1234" in restored  # SSN last-4
    assert "123 Main St" in restored
    assert "801-555-1234" in restored
    assert "john@example.com" in restored
    assert "201234567" in restored

    # DOB is normalized to ISO on the way through — either the original
    # slash form or the ISO form is acceptable.
    assert ("06/15/1985" in restored) or ("1985-06-15" in restored)

    # And PII should be GONE from the redacted text (no substring
    # leakage of real values into what Claude sees).
    for real in ["John", "Smith", "Robert",
                 "1234", "1985",
                 "john@example.com",
                 "801-555-1234", "201234567"]:
        assert real not in redacted, f"leak: {real!r} still in redacted text"


def test_substitute_parsed_replaces_name_parts_and_dates():
    """Simulate what happens after Claude returns JSON: real values
    should replace the pseudonyms field-by-field."""
    text = (
        "Name: John Robert Smith\n"
        "DOB: 06/15/1985\n"
        "SSN: XXX-XX-1234\n"
    )
    _, pmap = redact(text)

    # Simulate parser output using the FAKE identity Claude would see
    name_ent = next(e for e in pmap.entities if e.kind == "name")
    dob_ent = next(e for e in pmap.entities if e.kind == "dob")
    ssn_ent = next(e for e in pmap.entities if e.kind == "ssn")

    fake_parsed = {
        "subject": {
            "first_name": name_ent.parts["_fake_first"],
            "middle_name": name_ent.parts.get("_fake_middle"),
            "last_name": name_ent.parts["_fake_last"],
            "dob": dob_ent.fake,
            "ssn_last4": ssn_ent.fake,
        },
        "records": [],
    }

    restored = pmap.substitute_parsed(fake_parsed)
    assert restored["subject"]["first_name"] == "John"
    assert restored["subject"]["last_name"] == "Smith"
    assert restored["subject"]["middle_name"] == "Robert"
    # DOB is normalized to ISO during redaction (so substitution back
    # yields an ISO-parseable date; e.g. "October 5, 1976" would round-trip
    # to "1976-10-05" — the deterministic engine expects ISO).
    assert restored["subject"]["dob"] == "1985-06-15"
    assert restored["subject"]["ssn_last4"] == "1234"


# ---------------------------------------------------------------------------
# PIIMap stats + summary
# ---------------------------------------------------------------------------


def test_stats_counts_by_kind():
    text = (
        "Name: John Smith\n"
        "Co-defendant: Jane Doe\n"
        "DOB: 06/15/1985\n"
        "SSN: XXX-XX-1234\n"
        "Case Number: 20-1234\n"
    )
    _, pmap = redact(text)
    stats = pmap.stats()
    assert stats.get("name") == 2
    assert stats.get("dob") == 1
    assert stats.get("ssn") == 1
    assert stats.get("case") == 1


def test_empty_input():
    redacted, pmap = redact("")
    assert redacted == ""
    assert pmap.stats() == {}


def test_input_with_no_pii_passes_through():
    text = "Grand Theft was reported to the 3rd District Court in Utah."
    redacted, pmap = redact(text)
    # Some noise is fine \u2014 a stray Utah proper noun might get caught, but
    # nothing that changes the structural meaning should be gone.
    assert "Grand Theft" in redacted
    assert "District Court" in redacted


def test_stopword_labels_are_ignored():
    text = "Name: N/A\nDefendant: Unknown"
    _, pmap = redact(text)
    assert pmap.stats().get("name", 0) == 0
