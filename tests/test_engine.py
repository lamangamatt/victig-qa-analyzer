"""Unit tests for the QA analyzer engine.

Run with:
    PYTHONPATH=src python3 -m unittest discover tests

or one at a time:
    PYTHONPATH=src python3 -m unittest tests.test_engine.TestOffenseLevel
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

# Allow running from repo root without install
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from qa_analyzer.decision import analyze_record
from qa_analyzer.models import (
    ClientProfile,
    CriminalRecord,
    DecisionOutcome,
    Disposition,
    Gender,
    OffenseLevel,
    Subject,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXED_TODAY = date(2026, 7, 24)


def make_subject(**overrides) -> Subject:
    defaults = dict(
        first_name="John",
        last_name="Smith",
        middle_name="Robert",
        dob=date(1985, 6, 15),
        gender=Gender.MALE,
        name_grade=45,
        address_history=[{"state": "UT", "county": "Salt Lake"}],
    )
    defaults.update(overrides)
    return Subject(**defaults)


def make_record(**overrides) -> CriminalRecord:
    defaults = dict(
        record_id="TEST-001",
        source="County Court",
        source_confirmed=True,
        charge_description="Test charge",
        offense_level=OffenseLevel.FELONY,
        disposition=Disposition.CONVICTED,
        arrest_date=date(2022, 1, 1),
        disposition_date=date(2022, 6, 1),
        state="UT",
        county="Salt Lake",
        case_number="TEST-CASE",
        record_first_name="John",
        record_last_name="Smith",
        record_middle_name="Robert",
        record_dob=date(1985, 6, 15),
        record_gender=Gender.MALE,
        record_address_state="UT",
    )
    defaults.update(overrides)
    return CriminalRecord(**defaults)


def analyze(record, subject=None, client=None, others=None):
    return analyze_record(
        record,
        subject or make_subject(),
        client,
        today=FIXED_TODAY,
        other_records_on_report=others,
    )


# ---------------------------------------------------------------------------
# Test 1: Offense level
# ---------------------------------------------------------------------------


class TestOffenseLevel(unittest.TestCase):
    def test_felony_passes(self):
        d = analyze(make_record(offense_level=OffenseLevel.FELONY))
        self.assertEqual(d.outcome, DecisionOutcome.REPORT)

    def test_misdemeanor_passes(self):
        d = analyze(make_record(
            offense_level=OffenseLevel.MISDEMEANOR,
            disposition_date=date(2024, 1, 1),  # keep within 10yr FCRA window
        ))
        self.assertEqual(d.outcome, DecisionOutcome.REPORT)

    def test_traffic_infraction_excluded(self):
        d = analyze(make_record(offense_level=OffenseLevel.TRAFFIC_INFRACTION))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_ordinance_excluded(self):
        d = analyze(make_record(offense_level=OffenseLevel.ORDINANCE))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_pa_summary_offense_excluded(self):
        d = analyze(make_record(
            state="PA",
            offense_level=OffenseLevel.SUMMARY_OFFENSE,
        ))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_nj_petty_disorderly_excluded(self):
        d = analyze(make_record(
            state="NJ",
            offense_level=OffenseLevel.PETTY_DISORDERLY,
        ))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_unknown_level_escalates(self):
        d = analyze(make_record(offense_level=OffenseLevel.UNKNOWN))
        self.assertEqual(d.outcome, DecisionOutcome.ESCALATE)

    def test_indiana_amended_felony_excluded(self):
        d = analyze(make_record(
            state="IN",
            offense_level=OffenseLevel.FELONY,
            is_amended_from_felony=True,
        ))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)


# ---------------------------------------------------------------------------
# Test 3: Disposition
# ---------------------------------------------------------------------------


class TestDisposition(unittest.TestCase):
    def test_dismissed_excluded(self):
        d = analyze(make_record(disposition=Disposition.DISMISSED))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_acquitted_excluded(self):
        d = analyze(make_record(disposition=Disposition.ACQUITTED))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_expunged_excluded(self):
        d = analyze(make_record(disposition=Disposition.EXPUNGED))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_sealed_excluded(self):
        d = analyze(make_record(disposition=Disposition.SEALED))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_arrest_only_excluded(self):
        d = analyze(make_record(disposition=Disposition.ARREST_ONLY))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_adjudication_withheld_excluded(self):
        d = analyze(make_record(disposition=Disposition.ADJUDICATION_WITHHELD))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_juvenile_excluded(self):
        d = analyze(make_record(disposition=Disposition.JUVENILE))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_no_disposition_escalates(self):
        d = analyze(make_record(disposition=Disposition.NO_DISPOSITION))
        self.assertEqual(d.outcome, DecisionOutcome.ESCALATE)

    def test_pending_reportable(self):
        d = analyze(make_record(
            offense_level=OffenseLevel.MISDEMEANOR,
            disposition=Disposition.PENDING,
            arrest_date=date(2024, 1, 1),
        ))
        self.assertEqual(d.outcome, DecisionOutcome.REPORT)

    def test_nolo_contendere_treated_as_conviction(self):
        d = analyze(make_record(disposition=Disposition.NOLO_CONTENDERE))
        self.assertEqual(d.outcome, DecisionOutcome.REPORT)


# ---------------------------------------------------------------------------
# Test 2: Scope — FCRA + state
# ---------------------------------------------------------------------------


class TestScopeFCRA(unittest.TestCase):
    def test_misdemeanor_within_10yr_reports(self):
        d = analyze(make_record(
            offense_level=OffenseLevel.MISDEMEANOR,
            disposition_date=date(2020, 1, 1),  # ~6yr ago
        ))
        self.assertEqual(d.outcome, DecisionOutcome.REPORT)

    def test_misdemeanor_beyond_10yr_excluded(self):
        d = analyze(make_record(
            offense_level=OffenseLevel.MISDEMEANOR,
            disposition_date=date(2012, 1, 1),  # ~14yr ago
        ))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_felony_indefinite_reports(self):
        d = analyze(make_record(
            offense_level=OffenseLevel.FELONY,
            disposition_date=date(2000, 1, 1),  # ~26yr ago
        ))
        self.assertEqual(d.outcome, DecisionOutcome.REPORT)

    def test_pending_within_7yr_reports(self):
        d = analyze(make_record(
            offense_level=OffenseLevel.MISDEMEANOR,
            disposition=Disposition.PENDING,
            arrest_date=date(2024, 1, 1),
        ))
        self.assertEqual(d.outcome, DecisionOutcome.REPORT)

    def test_pending_beyond_7yr_excluded(self):
        d = analyze(make_record(
            offense_level=OffenseLevel.MISDEMEANOR,
            disposition=Disposition.PENDING,
            arrest_date=date(2015, 1, 1),  # ~11yr ago
        ))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)


# ---------------------------------------------------------------------------
# State-specific rules
# ---------------------------------------------------------------------------


class TestStateRules(unittest.TestCase):
    def test_ak_pending_excluded(self):
        d = analyze(make_record(
            state="AK",
            offense_level=OffenseLevel.MISDEMEANOR,
            disposition=Disposition.PENDING,
            arrest_date=date(2024, 1, 1),
        ))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_ky_pending_excluded(self):
        d = analyze(make_record(
            state="KY",
            offense_level=OffenseLevel.MISDEMEANOR,
            disposition=Disposition.PENDING,
            arrest_date=date(2024, 1, 1),
        ))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_ny_pending_reportable(self):
        d = analyze(make_record(
            state="NY",
            offense_level=OffenseLevel.MISDEMEANOR,
            disposition=Disposition.PENDING,
            arrest_date=date(2024, 1, 1),
        ))
        self.assertEqual(d.outcome, DecisionOutcome.REPORT)

    def test_ca_pending_reportable(self):
        d = analyze(make_record(
            state="CA",
            offense_level=OffenseLevel.MISDEMEANOR,
            disposition=Disposition.PENDING,
            arrest_date=date(2024, 1, 1),
        ))
        self.assertEqual(d.outcome, DecisionOutcome.REPORT)

    def test_ma_misdemeanor_3yr_excluded(self):
        d = analyze(make_record(
            state="MA",
            offense_level=OffenseLevel.MISDEMEANOR,
            disposition_date=date(2020, 1, 1),  # ~6yr ago
        ))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_ma_misdemeanor_within_3yr_reports(self):
        d = analyze(make_record(
            state="MA",
            offense_level=OffenseLevel.MISDEMEANOR,
            disposition_date=date(2024, 6, 1),  # ~2yr ago
        ))
        self.assertEqual(d.outcome, DecisionOutcome.REPORT)

    def test_ma_felony_7yr_excluded(self):
        d = analyze(make_record(
            state="MA",
            offense_level=OffenseLevel.FELONY,
            disposition_date=date(2015, 1, 1),  # ~11yr ago
        ))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_hi_felony_7yr_disposition_only(self):
        d = analyze(make_record(
            state="HI",
            offense_level=OffenseLevel.FELONY,
            disposition_date=date(2022, 1, 1),  # ~4.5yr ago
        ))
        self.assertEqual(d.outcome, DecisionOutcome.REPORT)

    def test_hi_misdemeanor_5yr_excluded(self):
        d = analyze(make_record(
            state="HI",
            offense_level=OffenseLevel.MISDEMEANOR,
            disposition_date=date(2020, 1, 1),  # ~6yr ago
        ))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_ar_pending_3yr(self):
        d = analyze(make_record(
            state="AR",
            offense_level=OffenseLevel.MISDEMEANOR,
            disposition=Disposition.PENDING,
            arrest_date=date(2022, 1, 1),  # ~4.5yr ago > 3
        ))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_ny_salary_below_cap_7yr(self):
        subj = make_subject(annual_salary=20000)  # below $25k cap
        d = analyze(
            make_record(
                state="NY",
                offense_level=OffenseLevel.FELONY,
                disposition_date=date(2016, 1, 1),  # ~10.5yr ago
            ),
            subj,
        )
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_ny_salary_above_cap_no_restriction(self):
        subj = make_subject(annual_salary=80000)  # above cap
        d = analyze(
            make_record(
                state="NY",
                offense_level=OffenseLevel.FELONY,
                disposition_date=date(2016, 1, 1),  # ~10.5yr ago, felony indefinite
            ),
            subj,
        )
        self.assertEqual(d.outcome, DecisionOutcome.REPORT)


# ---------------------------------------------------------------------------
# California marijuana rule
# ---------------------------------------------------------------------------


class TestCaliforniaMarijuana(unittest.TestCase):
    def test_ca_mj_over_2yr_excluded(self):
        d = analyze(make_record(
            state="CA",
            offense_level=OffenseLevel.MISDEMEANOR,
            disposition_date=date(2022, 1, 1),  # ~4.5yr ago
            is_marijuana_possession=True,
        ))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_ca_mj_within_2yr_reports(self):
        d = analyze(make_record(
            state="CA",
            offense_level=OffenseLevel.MISDEMEANOR,
            disposition_date=date(2025, 1, 1),  # ~1.5yr ago
            is_marijuana_possession=True,
        ))
        self.assertEqual(d.outcome, DecisionOutcome.REPORT)


# ---------------------------------------------------------------------------
# Illinois adjudication-withheld rule
# ---------------------------------------------------------------------------


class TestIllinoisRule(unittest.TestCase):
    def test_il_adj_withheld_alone_excluded(self):
        rec = make_record(
            state="IL",
            disposition=Disposition.ADJUDICATION_WITHHELD,
        )
        d = analyze(rec, others=[rec])
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_il_adj_withheld_with_other_conviction_still_excluded_by_test3(self):
        # Even if other conviction exists, adjudication_withheld itself is
        # not reportable per Test 3 (nationwide rule).
        rec1 = make_record(
            record_id="R1",
            state="IL",
            disposition=Disposition.ADJUDICATION_WITHHELD,
        )
        rec2 = make_record(
            record_id="R2",
            state="IL",
            disposition=Disposition.CONVICTED,
        )
        d = analyze(rec1, others=[rec1, rec2])
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)


# ---------------------------------------------------------------------------
# Matching Policy — Level 1/2/3
# ---------------------------------------------------------------------------


class TestMatching(unittest.TestCase):
    def test_gender_mismatch_excludes(self):
        subj = make_subject(gender=Gender.FEMALE)
        rec = make_record(record_gender=Gender.MALE)
        d = analyze(rec, subj)
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_race_mismatch_excludes(self):
        subj = make_subject(race="White")
        rec = make_record(record_race="Black")
        d = analyze(rec, subj)
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_only_two_identifiers_uncommon_name_passes(self):
        subj = make_subject(name_grade=30, middle_name=None)
        rec = make_record(record_middle_name=None)
        d = analyze(rec, subj)
        self.assertEqual(d.outcome, DecisionOutcome.REPORT)

    def test_only_two_identifiers_common_name_excludes(self):
        subj = make_subject(name_grade=75, middle_name=None)
        rec = make_record(record_middle_name=None)
        d = analyze(rec, subj)
        # Only 2 of 4 (name+dob), common name needs 3 → EXCLUDE
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_common_name_no_locational_match_escalates(self):
        subj = make_subject(
            name_grade=75,
            address_history=[{"state": "UT", "county": "Utah"}],
        )
        rec = make_record(record_address_state="TX")
        d = analyze(rec, subj)
        self.assertEqual(d.outcome, DecisionOutcome.ESCALATE)

    def test_no_name_grade_escalates(self):
        subj = make_subject(name_grade=None)
        d = analyze(make_record(), subj)
        self.assertEqual(d.outcome, DecisionOutcome.ESCALATE)

    def test_middle_initial_match(self):
        subj = make_subject(middle_name="Robert")
        rec = make_record(record_middle_name="R")
        d = analyze(rec, subj)
        self.assertEqual(d.outcome, DecisionOutcome.REPORT)


# ---------------------------------------------------------------------------
# Source confirmation (SOP §2)
# ---------------------------------------------------------------------------


class TestSourceConfirmation(unittest.TestCase):
    def test_unconfirmed_source_escalates(self):
        d = analyze(make_record(source_confirmed=False, source="NetPlus"))
        self.assertEqual(d.outcome, DecisionOutcome.ESCALATE)

    def test_unconfirmed_but_hard_fail_excludes(self):
        d = analyze(make_record(
            source_confirmed=False,
            disposition=Disposition.DISMISSED,
        ))
        # Hard-fail wins over escalate.
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)


# ---------------------------------------------------------------------------
# Client restrictions
# ---------------------------------------------------------------------------


class TestClientRestrictions(unittest.TestCase):
    def test_client_5yr_felony_cap_excludes(self):
        client = ClientProfile(
            client_id="C1", client_name="Test",
            max_years_felony=5,
        )
        d = analyze(
            make_record(
                offense_level=OffenseLevel.FELONY,
                disposition_date=date(2018, 1, 1),  # ~8.5yr ago
            ),
            client=client,
        )
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_client_5yr_felony_cap_reports_within(self):
        client = ClientProfile(
            client_id="C1", client_name="Test",
            max_years_felony=5,
        )
        d = analyze(
            make_record(
                offense_level=OffenseLevel.FELONY,
                disposition_date=date(2023, 1, 1),  # ~3.5yr ago
            ),
            client=client,
        )
        self.assertEqual(d.outcome, DecisionOutcome.REPORT)


# ---------------------------------------------------------------------------
# Precedence: EXCLUDE > ESCALATE > REPORT
# ---------------------------------------------------------------------------


class TestPrecedence(unittest.TestCase):
    def test_hard_fail_wins_over_escalate(self):
        # Offense=UNKNOWN escalates, but dismissed disposition hard-fails.
        d = analyze(make_record(
            offense_level=OffenseLevel.UNKNOWN,
            disposition=Disposition.DISMISSED,
        ))
        self.assertEqual(d.outcome, DecisionOutcome.EXCLUDE)

    def test_escalate_when_only_soft_failures(self):
        d = analyze(make_record(
            offense_level=OffenseLevel.UNKNOWN,
        ))
        self.assertEqual(d.outcome, DecisionOutcome.ESCALATE)


if __name__ == "__main__":
    unittest.main()
