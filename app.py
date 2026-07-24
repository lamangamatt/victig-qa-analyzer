"""VICTIG QA Analyzer — Streamlit UI

Primary flow: operator pastes candidate + criminal record data,
LLM parser extracts structured data, operator reviews/edits,
deterministic engine returns REPORT / EXCLUDE / ESCALATE per record.

Deploy: push to GitHub, connect to share.streamlit.io.
Set ANTHROPIC_API_KEY in Streamlit Secrets to enable paste parser.
Local: streamlit run app.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "src"))

import streamlit as st  # noqa: E402

from qa_analyzer import parser  # noqa: E402
from qa_analyzer.decision import analyze_record  # noqa: E402
from qa_analyzer.models import (  # noqa: E402
    ClientProfile,
    CriminalRecord,
    Decision,
    DecisionOutcome,
    Disposition,
    Gender,
    OffenseLevel,
    Subject,
)
from qa_analyzer.state_law import all_states, get_state_rule  # noqa: E402


# ---------------------------------------------------------------------------
# Page config + CSS
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="VICTIG QA Analyzer",
    page_icon="⚖️",
    layout="wide",
)

st.markdown(
    """
    <style>
        .outcome-report {
            color: #ffffff;
            background-color: #198754;
            padding: 18px 24px;
            border-radius: 8px;
            font-size: 1.5rem;
            font-weight: 700;
            text-align: center;
        }
        .outcome-exclude {
            color: #ffffff;
            background-color: #dc3545;
            padding: 18px 24px;
            border-radius: 8px;
            font-size: 1.5rem;
            font-weight: 700;
            text-align: center;
        }
        .outcome-escalate {
            color: #212529;
            background-color: #ffc107;
            padding: 18px 24px;
            border-radius: 8px;
            font-size: 1.5rem;
            font-weight: 700;
            text-align: center;
        }
        .rule-pass {
            background-color: #d1e7dd;
            border-left: 5px solid #198754;
            padding: 12px 16px;
            margin: 8px 0;
            border-radius: 4px;
        }
        .rule-fail {
            background-color: #f8d7da;
            border-left: 5px solid #dc3545;
            padding: 12px 16px;
            margin: 8px 0;
            border-radius: 4px;
        }
        .rule-escalate {
            background-color: #fff3cd;
            border-left: 5px solid #ffc107;
            padding: 12px 16px;
            margin: 8px 0;
            border-radius: 4px;
        }
        .rule-detail { color: #4a4a4a; margin-top: 6px; }
        .sop-ref {
            color: #6c757d;
            font-size: 0.85rem;
            font-style: italic;
            margin-top: 4px;
        }
        .footer-note {
            color: #6c757d;
            font-size: 0.85rem;
            margin-top: 24px;
            padding-top: 12px;
            border-top: 1px solid #dee2e6;
        }
        .summary-metric {
            background-color: #f8f9fa;
            border-radius: 8px;
            padding: 16px;
            text-align: center;
        }
        .confidence-high { color: #198754; font-weight: 600; }
        .confidence-medium { color: #b58900; font-weight: 600; }
        .confidence-low { color: #dc3545; font-weight: 600; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Enum label maps for select widgets
# ---------------------------------------------------------------------------

OFFENSE_LEVEL_LABELS = {
    OffenseLevel.FELONY: "Felony",
    OffenseLevel.MISDEMEANOR: "Misdemeanor",
    OffenseLevel.PETTY_MISDEMEANOR: "Petty Misdemeanor (MN)",
    OffenseLevel.MINOR_MISDEMEANOR: "Minor Misdemeanor (OH)",
    OffenseLevel.TRAFFIC_INFRACTION: "Traffic Infraction",
    OffenseLevel.ORDINANCE: "Ordinance",
    OffenseLevel.FORFEITURE_U: "Forfeiture U (WI)",
    OffenseLevel.SUMMARY_OFFENSE: "Summary Offense (PA)",
    OffenseLevel.PETTY_DISORDERLY: "Petty Disorderly (NJ)",
    OffenseLevel.UNKNOWN: "Unknown",
}

DISPOSITION_LABELS = {
    Disposition.CONVICTED: "Convicted",
    Disposition.GUILTY: "Guilty",
    Disposition.NOLO_CONTENDERE: "Nolo Contendere",
    Disposition.PENDING: "Pending",
    Disposition.DEFERRED: "Deferred (active)",
    Disposition.DIVERSION: "Diversion (active)",
    Disposition.FIRST_OFFENDER: "First Offender (active)",
    Disposition.DISMISSED: "Dismissed",
    Disposition.ACQUITTED: "Acquitted",
    Disposition.NOT_GUILTY: "Not Guilty",
    Disposition.ADJUDICATION_WITHHELD: "Adjudication Withheld",
    Disposition.DEFERRED_COMPLETED: "Deferred (completed)",
    Disposition.EXPUNGED: "Expunged",
    Disposition.SEALED: "Sealed",
    Disposition.JUVENILE: "Juvenile",
    Disposition.ARREST_ONLY: "Arrest-only",
    Disposition.NO_DISPOSITION: "No disposition available",
    Disposition.UNKNOWN: "Unknown",
}

GENDER_LABELS = {
    None: "— not specified —",
    Gender.MALE: "Male",
    Gender.FEMALE: "Female",
    Gender.UNKNOWN: "Unknown",
}

US_STATES = [
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA",
    "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY",
    "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX",
    "UT", "VT", "VA", "WA", "WV", "WI", "WY",
]


# ---------------------------------------------------------------------------
# Sample paste (in the sidebar as a copy-paste starter)
# ---------------------------------------------------------------------------

SAMPLE_PASTE = """CANDIDATE:
Name: John Robert Smith
DOB: 06/15/1985
SSN: XXX-XX-1234
Gender: Male
Address: 123 Main St, Salt Lake City, UT 84101 (2015-present)
Prior: Los Angeles, CA (2018-2022)
Annual Salary: $60,000
NameGrade: 45

CRIMINAL RECORD:
Source: Salt Lake County Court (verified)
Case Number: 201234567
Charge: Grand Theft (Felony)
Court: 3rd District Court, Salt Lake County, UT
Arrest Date: 05/10/2020
Filed: 05/15/2020
Disposition: Convicted
Disposition Date: 11/20/2020
Defendant Name: John R Smith
Defendant DOB: 06/15/1985
Gender: M
"""


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------


def render_outcome(outcome: DecisionOutcome, record_id: str = ""):
    cls = {
        DecisionOutcome.REPORT: "outcome-report",
        DecisionOutcome.EXCLUDE: "outcome-exclude",
        DecisionOutcome.ESCALATE: "outcome-escalate",
    }[outcome]
    icon = {
        DecisionOutcome.REPORT: "✅ REPORT",
        DecisionOutcome.EXCLUDE: "❌ EXCLUDE",
        DecisionOutcome.ESCALATE: "⚠️ ESCALATE",
    }[outcome]
    label = f"{icon}  ·  {record_id}" if record_id else icon
    st.markdown(f"<div class='{cls}'>{label}</div>", unsafe_allow_html=True)


def render_rule_result(r):
    if r.escalate:
        cls, mark = "rule-escalate", "⚠️"
    elif r.passed:
        cls, mark = "rule-pass", "✅"
    else:
        cls, mark = "rule-fail", "❌"
    st.markdown(
        f"""
        <div class='{cls}'>
            <strong>{mark} {r.test_name}</strong>
            <div class='rule-detail'>{r.detail}</div>
            <div class='sop-ref'>→ {r.sop_reference}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_decision(decision: Decision, record: CriminalRecord, subject: Subject):
    render_outcome(decision.outcome, record.record_id or "record")
    st.write("")

    col_a, col_b, col_c = st.columns(3)
    with col_a:
        st.markdown("**Subject**")
        parts = [subject.first_name]
        if subject.middle_name:
            parts.append(subject.middle_name)
        parts.append(subject.last_name)
        st.text(" ".join(parts))
        if subject.dob:
            st.text(f"DOB: {subject.dob.isoformat()}")
    with col_b:
        st.markdown("**Charge**")
        st.text(record.charge_description or "(unspecified)")
        st.text(f"{record.offense_level.value} · {record.disposition.value}")
    with col_c:
        st.markdown("**Court**")
        st.text(f"{record.state} — {record.county}")
        if record.case_number:
            st.text(f"Case: {record.case_number}")

    if decision.controlling_date:
        st.info(
            f"**Controlling date:** {decision.controlling_date.isoformat()} — "
            f"{decision.controlling_date_reason}"
        )

    for r in decision.rule_results:
        render_rule_result(r)

    if decision.matching_score:
        with st.expander("Matching Policy detail (SOP §8)"):
            m = decision.matching_score
            st.markdown(
                f"**Level 1 matches:** {m['level_one_match_count']}/4  ·  "
                f"**NameGrade:** {m['name_grade']}  (threshold "
                f"{m['namegrade_threshold']}; common name = {m['common_name']})"
            )
            for k, v in m["level_one_matches"].items():
                symbol = {True: "✅", False: "❌", None: "❓"}[v]
                st.text(f"  {symbol} {k}")
            if m["level_two_disqualifiers"]:
                st.error(
                    "Level 2 disqualifiers (excludes record): "
                    + "; ".join(m["level_two_disqualifiers"])
                )
            if m["level_three_flags"]:
                st.warning("Level 3 red flags: " + "; ".join(m["level_three_flags"]))

    if decision.state_rules_applied:
        with st.expander(f"State-law overlays for {record.state or '?'}"):
            for note in decision.state_rules_applied:
                st.text(f"• {note}")

    if decision.warnings:
        for w in decision.warnings:
            st.warning(w)
    if decision.escalation_reasons:
        st.markdown("**Escalation reasons:**")
        for e in decision.escalation_reasons:
            st.text(f"• {e}")


# ---------------------------------------------------------------------------
# Preview + edit widgets (for LLM-parsed data before analysis)
# ---------------------------------------------------------------------------


def _safe_date(v, fallback: date = None) -> date:
    return v if isinstance(v, date) else (fallback or date.today())


def edit_subject_widget(subject: Subject, key_prefix: str = "s") -> Subject:
    """Compact editor for the parsed Subject; returns updated Subject."""
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        first = st.text_input("First name*", value=subject.first_name, key=f"{key_prefix}_first")
    with c2:
        middle = st.text_input("Middle", value=subject.middle_name or "", key=f"{key_prefix}_middle")
    with c3:
        last = st.text_input("Last name*", value=subject.last_name, key=f"{key_prefix}_last")
    with c4:
        dob = st.date_input(
            "DOB*",
            value=subject.dob or date(1985, 1, 1),
            min_value=date(1920, 1, 1),
            max_value=date.today(),
            key=f"{key_prefix}_dob",
        )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        ssn = st.text_input(
            "SSN last 4", value=subject.ssn_last4 or "", max_chars=4,
            key=f"{key_prefix}_ssn",
        )
    with c2:
        gender = st.selectbox(
            "Gender", options=list(GENDER_LABELS.keys()),
            format_func=lambda g: GENDER_LABELS[g],
            index=list(GENDER_LABELS.keys()).index(subject.gender)
                if subject.gender in GENDER_LABELS else 0,
            key=f"{key_prefix}_gender",
        )
    with c3:
        salary = st.number_input(
            "Annual salary", min_value=0, max_value=1_000_000,
            value=int(subject.annual_salary or 0), step=5000,
            key=f"{key_prefix}_salary",
        )
    with c4:
        name_grade = st.number_input(
            "NameGrade", min_value=0, max_value=100,
            value=int(subject.name_grade) if subject.name_grade is not None else 50,
            step=1, help="≥58 = common name, needs 3 identifiers",
            key=f"{key_prefix}_ng",
        )

    # Address history — flattened to a comma list of states
    prior_states = ",".join(
        sorted({(a.get("state") or "").upper() for a in subject.address_history
                if a.get("state")})
    )
    addr_states = st.text_input(
        "Prior states (comma-separated)", value=prior_states,
        help="For Level 3 locational matching",
        key=f"{key_prefix}_addr",
    )
    address_history = [
        {"state": s.strip().upper(), "county": ""}
        for s in addr_states.split(",") if s.strip()
    ]

    return Subject(
        first_name=first.strip(),
        last_name=last.strip(),
        middle_name=middle.strip() or None,
        dob=dob,
        ssn_last4=ssn.strip() or None,
        gender=gender,
        race=subject.race,
        address_history=address_history,
        annual_salary=float(salary) if salary else None,
        name_grade=int(name_grade),
    )


def edit_record_widget(rec: CriminalRecord, idx: int) -> CriminalRecord:
    """Compact editor for one parsed CriminalRecord."""
    key = f"r{idx}"

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        case_num = st.text_input("Case number", value=rec.case_number,
                                 key=f"{key}_case")
    with c2:
        source = st.text_input("Source", value=rec.source, key=f"{key}_src")
    with c3:
        source_confirmed = st.checkbox(
            "Source confirmed",
            value=rec.source_confirmed,
            key=f"{key}_srcconf",
            help="DB-only hits must be confirmed with court (SOP §2).",
        )
    with c4:
        state_idx = US_STATES.index(rec.state) if rec.state in US_STATES else 0
        state = st.selectbox("State*", options=US_STATES, index=state_idx,
                             key=f"{key}_state")

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        charge = st.text_input("Charge description",
                               value=rec.charge_description, key=f"{key}_charge")
    with c2:
        offense_level = st.selectbox(
            "Offense level*",
            options=list(OFFENSE_LEVEL_LABELS.keys()),
            format_func=lambda l: OFFENSE_LEVEL_LABELS[l],
            index=list(OFFENSE_LEVEL_LABELS.keys()).index(rec.offense_level)
                if rec.offense_level in OFFENSE_LEVEL_LABELS else 0,
            key=f"{key}_lvl",
        )
    with c3:
        county = st.text_input("County", value=rec.county, key=f"{key}_county")

    c1, c2, c3 = st.columns(3)
    with c1:
        disposition = st.selectbox(
            "Disposition*",
            options=list(DISPOSITION_LABELS.keys()),
            format_func=lambda d: DISPOSITION_LABELS[d],
            index=list(DISPOSITION_LABELS.keys()).index(rec.disposition)
                if rec.disposition in DISPOSITION_LABELS else 0,
            key=f"{key}_disp",
        )
    with c2:
        arrest_date = st.date_input(
            "Arrest date",
            value=rec.arrest_date, min_value=date(1970, 1, 1),
            max_value=date.today(), key=f"{key}_arrest",
        )
    with c3:
        disposition_date = st.date_input(
            "Disposition date",
            value=rec.disposition_date, min_value=date(1970, 1, 1),
            max_value=date.today(), key=f"{key}_dispdate",
        )

    c1, c2, c3 = st.columns(3)
    with c1:
        release_date = st.date_input(
            "Release date",
            value=rec.release_date, min_value=date(1970, 1, 1),
            max_value=date.today(), key=f"{key}_release",
        )
    with c2:
        parole_date = st.date_input(
            "Parole start",
            value=rec.parole_start_date, min_value=date(1970, 1, 1),
            max_value=date.today(), key=f"{key}_parole",
        )
    with c3:
        sentence_max = st.date_input(
            "Max sentence",
            value=rec.sentence_max_date, min_value=date(1970, 1, 1),
            max_value=date.today(), key=f"{key}_sentmax",
        )

    st.caption("Record-holder identifiers (from the charge record)")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        r_first = st.text_input("Rec first",
                                value=rec.record_first_name, key=f"{key}_rfirst")
    with c2:
        r_middle = st.text_input("Rec middle",
                                 value=rec.record_middle_name or "", key=f"{key}_rmiddle")
    with c3:
        r_last = st.text_input("Rec last",
                               value=rec.record_last_name, key=f"{key}_rlast")
    with c4:
        r_dob = st.date_input(
            "Rec DOB",
            value=rec.record_dob, min_value=date(1920, 1, 1),
            max_value=date.today(), key=f"{key}_rdob",
        )

    c1, c2, c3 = st.columns(3)
    with c1:
        r_gender = st.selectbox(
            "Rec gender",
            options=list(GENDER_LABELS.keys()),
            format_func=lambda g: GENDER_LABELS[g],
            index=list(GENDER_LABELS.keys()).index(rec.record_gender)
                if rec.record_gender in GENDER_LABELS else 0,
            key=f"{key}_rgender",
        )
    with c2:
        r_state_options = [""] + US_STATES
        r_state_idx = r_state_options.index(rec.record_address_state) \
            if rec.record_address_state in r_state_options else 0
        r_state = st.selectbox("Rec state", options=r_state_options,
                               index=r_state_idx, key=f"{key}_rstate")
    with c3:
        is_mj = st.checkbox(
            "CA non-felony marijuana",
            value=rec.is_marijuana_possession, key=f"{key}_mj",
        )

    return CriminalRecord(
        record_id=case_num or rec.record_id,
        source=source, source_confirmed=source_confirmed,
        charge_description=charge, offense_level=offense_level,
        disposition=disposition, arrest_date=arrest_date,
        disposition_date=disposition_date, release_date=release_date,
        parole_start_date=parole_date, sentence_max_date=sentence_max,
        state=state, county=county,
        court_name=rec.court_name, case_number=case_num,
        record_first_name=r_first, record_last_name=r_last,
        record_middle_name=r_middle or None,
        record_dob=r_dob, record_gender=r_gender,
        record_address_state=r_state or None,
        is_marijuana_possession=is_mj,
        is_amended_from_felony=rec.is_amended_from_felony,
    )


# ---------------------------------------------------------------------------
# Page: Paste & Analyze (default)
# ---------------------------------------------------------------------------


def _init_state():
    st.session_state.setdefault("parsed", None)
    st.session_state.setdefault("parse_error", None)
    st.session_state.setdefault("paste_text", "")


def _load_sample_cb():
    """Callback: populate paste_text with the sample. Runs before the
    text_area widget is instantiated on the next rerun."""
    st.session_state["paste_text"] = SAMPLE_PASTE
    st.session_state["parsed"] = None
    st.session_state["parse_error"] = None


def _reset_cb():
    """Callback: clear everything."""
    st.session_state["paste_text"] = ""
    st.session_state["parsed"] = None
    st.session_state["parse_error"] = None


def page_paste():
    _init_state()
    st.title("⚖️ VICTIG QA Analyzer")
    st.caption(
        "Paste candidate + criminal record data. The parser extracts a "
        "structured version for you to review, then the engine runs the "
        "4 SOP tests + state law + matching policy."
    )

    if not parser.is_available():
        st.warning(
            "🔑 **Parser not configured.** Add `ANTHROPIC_API_KEY` in the "
            "Streamlit Cloud app settings → **Secrets**, then reboot the "
            "app. In the meantime, use the **Manual entry** page in the "
            "sidebar to run analyses."
        )

    text = st.text_area(
        "Paste any format — plain text, JSON, court docket, system export, etc.",
        height=280,
        placeholder="Paste candidate + criminal record data here…",
        key="paste_text",
    )

    c1, c2, c3 = st.columns([1, 1, 3])
    with c1:
        parse_btn = st.button(
            "🔎 Parse", type="primary", use_container_width=True,
            disabled=not parser.is_available(),
        )
    with c2:
        st.button(
            "📋 Load sample", use_container_width=True,
            on_click=_load_sample_cb,
        )
    with c3:
        st.button(
            "♻️ Reset", use_container_width=True,
            on_click=_reset_cb,
        )

    if parse_btn:
        st.session_state["parsed"] = None
        st.session_state["parse_error"] = None
        try:
            with st.spinner("Extracting structured data…"):
                parsed = parser.parse(text)
            st.session_state["parsed"] = parsed
        except parser.ParserError as e:
            st.session_state["parse_error"] = str(e)

    if st.session_state["parse_error"]:
        st.error(st.session_state["parse_error"])
        return

    parsed = st.session_state["parsed"]
    if not parsed:
        return

    # Confidence + notes
    conf = parsed.get("confidence", "medium")
    conf_cls = {"high": "confidence-high",
                "medium": "confidence-medium",
                "low": "confidence-low"}[conf]
    st.markdown(
        f"**Parse confidence:** <span class='{conf_cls}'>{conf.upper()}</span>",
        unsafe_allow_html=True,
    )
    notes = parsed.get("notes") or []
    if notes:
        st.info("📝 Parser notes:\n\n" + "\n".join(f"- {n}" for n in notes))

    # Review + edit
    st.markdown("---")
    st.subheader("👤 Candidate (review & edit)")
    subject = parser.dict_to_subject(parsed.get("subject", {}))
    subject = edit_subject_widget(subject)

    st.subheader(f"📋 Records ({len(parsed.get('records', []))})")
    records_raw = parsed.get("records", [])
    if not records_raw:
        st.warning("No records extracted. Add or paste more data and try again.")
        return

    records = []
    for i, rd in enumerate(records_raw):
        with st.expander(
            f"Record {i+1}: {rd.get('charge_description', '(no charge)')}  "
            f"[{rd.get('state', '?')}]",
            expanded=False,
        ):
            rec = parser.dict_to_record(rd)
            rec = edit_record_widget(rec, i)
            records.append(rec)

    # Client
    client = parser.dict_to_client(parsed.get("client"))

    # Analyze
    st.markdown("---")
    if st.button("⚖️ Analyze all records", type="primary", use_container_width=True):
        st.markdown("### Results")

        # Run analysis
        results = []
        counts = {"REPORT": 0, "EXCLUDE": 0, "ESCALATE": 0}
        for rec in records:
            d = analyze_record(rec, subject, client, other_records_on_report=records)
            results.append((rec, d))
            counts[d.outcome.value] += 1

        # Summary counters
        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(
                f"<div class='summary-metric'>"
                f"<h2 style='color:#dc3545;margin:0'>{counts['EXCLUDE']}</h2>"
                f"<div>EXCLUDE</div></div>",
                unsafe_allow_html=True,
            )
        with c2:
            st.markdown(
                f"<div class='summary-metric'>"
                f"<h2 style='color:#b58900;margin:0'>{counts['ESCALATE']}</h2>"
                f"<div>ESCALATE</div></div>",
                unsafe_allow_html=True,
            )
        with c3:
            st.markdown(
                f"<div class='summary-metric'>"
                f"<h2 style='color:#198754;margin:0'>{counts['REPORT']}</h2>"
                f"<div>REPORT</div></div>",
                unsafe_allow_html=True,
            )
        st.write("")

        # Group by outcome in order: EXCLUDE, ESCALATE, REPORT
        outcome_order = [
            DecisionOutcome.EXCLUDE,
            DecisionOutcome.ESCALATE,
            DecisionOutcome.REPORT,
        ]
        outcome_meta = {
            DecisionOutcome.EXCLUDE: ("❌", "EXCLUDE", "#dc3545"),
            DecisionOutcome.ESCALATE: ("⚠️", "ESCALATE", "#b58900"),
            DecisionOutcome.REPORT: ("✅", "REPORT", "#198754"),
        }

        for outcome in outcome_order:
            group = [(rec, d) for rec, d in results if d.outcome == outcome]
            if not group:
                continue

            icon, label, color = outcome_meta[outcome]
            st.markdown(
                f"<h4 style='color:{color};margin-top:20px;margin-bottom:8px'>"
                f"{icon} {label} · {len(group)}</h4>",
                unsafe_allow_html=True,
            )

            for rec, d in group:
                # Compact expander title so the outcome is scannable
                charge = rec.charge_description or "(no charge)"
                title = (
                    f"{icon} {label}  ·  {rec.record_id or 'record'}  ·  "
                    f"{charge} [{rec.state or '?'}]"
                )
                with st.expander(title, expanded=False):
                    render_decision(d, rec, subject)

        # Downloadable JSON audit trail
        st.markdown("---")
        audit = {
            "subject": {
                "name": f"{subject.first_name} {subject.last_name}",
                "dob": subject.dob.isoformat() if subject.dob else None,
            },
            "decisions": [d.to_dict() for _, d in results],
            "counts": counts,
        }
        st.download_button(
            "⬇️ Download audit trail (JSON)",
            data=json.dumps(audit, indent=2),
            file_name=f"qa_audit_{subject.first_name}_{subject.last_name}.json",
            mime="application/json",
        )


# ---------------------------------------------------------------------------
# Page: Manual entry (fallback when parser unavailable or user prefers it)
# ---------------------------------------------------------------------------


def page_manual():
    st.title("📝 Manual entry")
    st.caption("Fallback: fill the form directly if you'd rather not paste.")

    # Reuse the widgets — one subject + one record
    st.subheader("👤 Candidate")
    default_subject = Subject(
        first_name="", last_name="", dob=date(1985, 1, 1),
        gender=None, name_grade=50, address_history=[],
    )
    subject = edit_subject_widget(default_subject, key_prefix="man_s")

    st.subheader("📋 Record")
    default_record = CriminalRecord(
        record_id="", source="County Court", source_confirmed=True,
        charge_description="", offense_level=OffenseLevel.FELONY,
        disposition=Disposition.CONVICTED,
        arrest_date=None, disposition_date=date(2020, 1, 1),
        state="UT", county="",
        record_first_name="", record_last_name="",
    )
    record = edit_record_widget(default_record, idx=0)

    st.subheader("🏢 Client restrictions (optional)")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        client_name = st.text_input("Client name")
    with c2:
        client_max_misd = st.number_input(
            "Misd cap (yrs)", min_value=0, max_value=30, value=0,
            help="0 = FCRA/state default",
        )
    with c3:
        client_max_fel = st.number_input(
            "Felony cap (yrs)", min_value=0, max_value=30, value=0,
            help="0 = FCRA/state default",
        )
    with c4:
        felonies_only = st.checkbox("Felonies only")

    client = None
    if client_name or client_max_misd or client_max_fel or felonies_only:
        client = ClientProfile(
            client_id=(client_name or "custom").replace(" ", "_")[:32],
            client_name=client_name,
            max_years_misdemeanor=client_max_misd or None,
            max_years_felony=client_max_fel or None,
            felonies_only=felonies_only,
        )

    if st.button("⚖️ Analyze", type="primary", use_container_width=True):
        if not subject.first_name or not subject.last_name:
            st.error("Please enter the candidate's first and last name.")
            return
        d = analyze_record(record, subject, client)
        st.markdown("---")
        render_decision(d, record, subject)

        st.download_button(
            "⬇️ Download decision as JSON",
            data=json.dumps(d.to_dict(), indent=2),
            file_name=f"decision_{record.record_id or 'manual'}.json",
            mime="application/json",
        )


# ---------------------------------------------------------------------------
# Page: State rules
# ---------------------------------------------------------------------------


def page_state_rules():
    st.title("📁 State rules table")
    st.caption(
        "Special limitations beyond FCRA. States not listed follow FCRA "
        "default (10yr misdemeanor, indefinite felony)."
    )

    for code in all_states():
        rule = get_state_rule(code)
        with st.expander(f"**{code}** — {rule.sop_reference}"):
            cols = st.columns(4)
            cols[0].metric(
                "Conviction cap",
                f"{rule.conviction_max_years}y" if rule.conviction_max_years else "—",
            )
            cols[1].metric(
                "Misd cap",
                f"{rule.misdemeanor_max_years}y" if rule.misdemeanor_max_years else "(same)",
            )
            cols[2].metric(
                "Pending cap",
                f"{rule.pending_max_years}y" if rule.pending_max_years else "—",
            )
            cols[3].metric(
                "Salary cap",
                f"${rule.salary_cap:,.0f}" if rule.salary_cap else "—",
            )
            if rule.exclusions:
                st.markdown("**Notes:**")
                for e in rule.exclusions:
                    st.text(f"• {e}")


# ---------------------------------------------------------------------------
# Page: About
# ---------------------------------------------------------------------------


def page_about():
    st.title("📖 About the QA Analyzer")
    st.markdown(
        """
        Automates the reportability decision a VICTIG QA researcher makes
        on every criminal record: pass the record through Kate's SOP
        (FCRA + state law + client restrictions + matching policy) and
        return REPORT / EXCLUDE / ESCALATE with a full audit trail.

        ### How it works

        1. **Paste** raw candidate + record data (any format).
        2. The **parser** (Claude) extracts structured JSON.
        3. You **review & edit** the extracted fields.
        4. The **deterministic engine** applies:
           - SOP §5 — 4 core tests (offense/scope/disposition/PII)
           - SOP §6 — controlling-date calculation
           - SOP §7 — 19 states with special rules
           - SOP §8 — Matching Policy Levels 1/2/3
           - SOP §18 — reporting guidelines (Ban-the-Box, salary caps)
        5. Get a per-record verdict with **SOP citations** for every rule.

        The LLM only does PARSING (variability). All judgment is
        deterministic — auditable for FCRA compliance review.

        ### Precedence rule

        1. Any hard-fail → **EXCLUDE**
        2. Any escalate flag → **ESCALATE**
        3. All pass → **REPORT**

        ### Configuration

        - `ANTHROPIC_API_KEY` in Streamlit Secrets — required for the
          paste parser. Not needed for Manual entry mode.

        ### Repo & source

        - GitHub: https://github.com/lamangamatt/victig-qa-analyzer
        - SOP: Kate Florez's QA SOP (referenced throughout)
        """
    )


# ---------------------------------------------------------------------------
# Sidebar & main
# ---------------------------------------------------------------------------


def sidebar():
    st.sidebar.title("⚖️ QA Analyzer")
    st.sidebar.caption("VICTIG SOP-driven reportability engine")

    page = st.sidebar.radio(
        "Navigation",
        options=[
            "📋 Paste & analyze",
            "📝 Manual entry",
            "📁 State rules",
            "📖 About",
        ],
        label_visibility="collapsed",
    )
    st.sidebar.divider()

    with st.sidebar:
        parser_ok = parser.is_available()
        st.markdown(
            f"**Parser:** {'🟢 Enabled' if parser_ok else '⚪ Disabled (no API key)'}"
        )
        st.divider()
        st.markdown("### Outcomes")
        st.markdown(
            """
            - <span style='color:#198754;font-weight:600'>REPORT</span>
              — all 4 tests pass
            - <span style='color:#dc3545;font-weight:600'>EXCLUDE</span>
              — one or more tests definitively fail
            - <span style='color:#ffc107;font-weight:600'>ESCALATE</span>
              — needs human review
            """,
            unsafe_allow_html=True,
        )
        st.divider()
        st.markdown("### The 4 SOP tests")
        st.markdown(
            """
            1. **Reportable offense** (felony/misdemeanor)
            2. **Within scope** (FCRA + state + client caps)
            3. **Reportable disposition** (conviction or active pending)
            4. **Sufficient PII** (2 IDs, or 3 if common name)
            """
        )

    return page


def main():
    page = sidebar()
    if page.startswith("📋"):
        page_paste()
    elif page.startswith("📝"):
        page_manual()
    elif page.startswith("📁"):
        page_state_rules()
    else:
        page_about()

    st.markdown(
        "<div class='footer-note'>VICTIG QA Analyzer v0.2 · "
        "SOP source: Kate Florez · "
        "Built for FCRA-compliant reportability review</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
