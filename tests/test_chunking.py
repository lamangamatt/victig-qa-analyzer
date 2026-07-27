"""Tests for the auto-chunking fallback in parser._split_by_record_boundary."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from qa_analyzer.parser import _split_by_record_boundary  # noqa: E402


def test_single_record_paste_returns_unchanged():
    text = (
        "Name: John Smith\n"
        "DOB: 06/15/1985\n"
        "Case Number: 20-1234\n"
        "Charge: Grand Theft\n"
    )
    chunks = _split_by_record_boundary(text)
    # With only one Case Number label, we can't chunk further; return one.
    assert len(chunks) == 1


def test_multi_record_numbered_headers_splits():
    text = (
        "CANDIDATE:\n"
        "Name: John Smith\n"
        "DOB: 06/15/1985\n"
        "\n"
        "Record 1:\n"
        "Case Number: A-100\n"
        "Charge: Theft\n"
        "\n"
        "Record 2:\n"
        "Case Number: A-200\n"
        "Charge: Assault\n"
        "\n"
        "Record 3:\n"
        "Case Number: A-300\n"
        "Charge: DUI\n"
    )
    chunks = _split_by_record_boundary(text)
    assert len(chunks) == 3
    # Each chunk should include the candidate header.
    for c in chunks:
        assert "Name: John Smith" in c
        assert "DOB: 06/15/1985" in c


def test_case_number_labels_split():
    text = (
        "Candidate: Jane Doe\n"
        "DOB: 01/01/1990\n"
        "\n"
        "Case Number: X-100\n"
        "Charge: A\n"
        "\n"
        "Case Number: X-200\n"
        "Charge: B\n"
    )
    chunks = _split_by_record_boundary(text)
    assert len(chunks) == 2
    for c in chunks:
        assert "Jane Doe" in c


def test_criminal_record_heading_splits():
    text = (
        "SUBJECT: Test Person\n"
        "DOB: 05/05/1975\n"
        "\n"
        "CRIMINAL RECORD #1\n"
        "Case: A\n"
        "Charge: A\n"
        "\n"
        "CRIMINAL RECORD #2\n"
        "Case: B\n"
        "Charge: B\n"
    )
    chunks = _split_by_record_boundary(text)
    assert len(chunks) == 2


def test_no_records_returns_single():
    text = "Just some narrative text without any obvious record markers."
    chunks = _split_by_record_boundary(text)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_candidate_header_preserved_in_every_chunk():
    text = (
        "CANDIDATE:\n"
        "Name: Multi Record Guy\n"
        "DOB: 10/10/1980\n"
        "SSN: XXX-XX-9999\n"
        "\n"
        "Record 1: Charge: Theft, Case: 1\n"
        "Record 2: Charge: Fraud, Case: 2\n"
        "Record 3: Charge: Assault, Case: 3\n"
        "Record 4: Charge: Vandalism, Case: 4\n"
    )
    chunks = _split_by_record_boundary(text)
    assert len(chunks) == 4
    for i, c in enumerate(chunks):
        assert "Multi Record Guy" in c, f"chunk {i} lost candidate header"
        assert f"Record {i+1}" in c
