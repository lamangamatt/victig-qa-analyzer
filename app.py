"""VICTIG QA Analyzer — Streamlit UI

Team-facing tool for reviewing criminal records against Kate's SOP
(FCRA + state law + client restrictions + matching policy).

Deploy: push to GitHub, connect to share.streamlit.io.
Local:  streamlit run app.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

# Make the src/qa_analyzer package importable when running from repo root
_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "src"))

import streamlit as st  # noqa: E402

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
            font-size: 2rem;
            font-weight: 700;
            text-align: center;
            box-shadow: 0 2px 6px rgba(0,0,0,0.1);
        }
        .outcome-exclude {
            color: #ffffff;
            background-color: #dc3545;
            padding: 18px 24px;
            border-radius: 8px;
            font-size: 2rem;
            font-weight: 700;
            text-align: center;
            box-shadow: 0 2px 6px rgba(0,0,0,0.1);
        }
        .outcome-escalate {
            color: #212529;
            background-color: #ffc107;
            padding: 18px 24px;
            border-radius: 8px;
            font-size: 2rem;
            font-weight: 700;
            text-align: center;
            box-shadow: 0 2px 6px rgba(0,0,0,0.1);
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
        .stat-box {
            background-color: #f8f9fa;
            border-radius: 8px;
            padding: 12px 16px;
            margin: 6px 0;
        }
        .footer-note {
            color: #6c757d;
            font-size: 0.85rem;
            margin-top: 24px;
            padding-top: 12px;
            border-top: 1px solid #dee2e6;
        }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Enum options for select boxes
OFFENSE_LEVEL_LABELS = {
    OffenseLevel.FELONY: "Felony",
    OffenseLevel.MISDEMEANOR: "Misdemeanor",
    OffenseLevel.PETTY_MISDEMEANOR: "Petty Misdemeanor (MN)",
    OffenseLevel.MINOR_MISDEMEANOR: "Minor Misdemeanor (OH)",
    OffenseLevel.TRAFFIC_INFRACTION: "Traffic Infraction",
    OffenseLevel.ORDINANCE: "Ordinance (noise/dogs/etc.)",
    OffenseLevel.FORFEITURE_U: "Forfeiture U (WI)",
    OffenseLevel.SUMMARY_OFFENSE: "Summary Offense (PA)",
    OffenseLevel.PETTY_DISORDERLY: "Petty Disorderly Persons (NJ)",
    OffenseLevel.UNKNOWN: "Unknown",
}

DISPOSITION_LABELS = {
    Disposition.CONVICTED: "Convicted",
    Disposition.GUILTY: "Guilty",
    Disposition.NOLO_CONTENDERE: "Nolo Contendere (No Contest)",
    Disposition.PENDING: "Pending",
    Disposition.DEFERRED: "Deferred (active)",
    Disposition.DIVERSION: "Diversion (active)",
    Disposition.FIRST_OFFENDER: "First Offender Program (active)",
    Disposition.DISMISSED: "Dismissed",
    Disposition.ACQUITTED: "Acquitted",
    Disposition.NOT_GUILTY: "Not Guilty",
    Disposition.ADJUDICATION_WITHHELD: "Adjudication Withheld",
    Disposition.DEFERRED_COMPLETED: "Deferred (successfully completed)",
    Disposition.EXPUNGED: "Expunged",
    Disposition.SEALED: "Sealed",
    Disposition.JUVENILE: "Juvenile-court adjudication",
    Disposition.ARREST_ONLY: "Arrest-only (no charges filed)",
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


def render_outcome(outcome: DecisionOutcome):
    cls = {
        DecisionOutcome.REPORT: "outcome-report",
        DecisionOutcome.EXCLUDE: "outcome-exclude",
        DecisionOutcome.ESCALATE: "outcome-escalate",
    }[outcome]
    label = {
        DecisionOutcome.REPORT: "✅ REPORT — All SOP tests passed",
        DecisionOutcome.EXCLUDE: "❌ EXCLUDE — Record cannot be reported",
        DecisionOutcome.ESCALATE: "⚠️ ESCALATE — Human review required",
    }[outcome]
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
            <strong>{mark} [{r.test_id}] {r.test_name}</strong>
            <div class='rule-detail'>{r.detail}</div>
            <div class='sop-ref'>→ {r.sop_reference}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_decision(decision: Decision, record: CriminalRecord, subject: Subject):
    st.markdown("### Outcome")
    render_outcome(decision.outcome)
    st.write("")

    # Two-column summary of the record
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

    st.markdown("### Rule results")
    for r in decision.rule_results:
        render_rule_result(r)

    # Matching detail
    if decision.matching_score:
        with st.expander("Matching Policy detail (SOP §8)"):
            m = decision.matching_score
            st.markdown(
                f"**Level 1 matches:** {m['level_one_match_count']}/4  "
                f"**NameGrade:** {m['name_grade']} (threshold "
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
                st.warning(
                    "Level 3 red flags: " + "; ".join(m["level_three_flags"])
                )

    # State law overlay
    if decision.state_rules_applied:
        with st.expander(f"State-law overlays for {record.state or '?'}"):
            for note in decision.state_rules_applied:
                st.text(f"• {note}")

    # Warnings and escalation
    if decision.warnings:
        for w in decision.warnings:
            st.warning(w)
    if decision.escalation_reasons:
        st.markdown("**Escalation reasons:**")
        for e in decision.escalation_reasons:
            st.text(f"• {e}")


# ---------------------------------------------------------------------------
# Input form
# ---------------------------------------------------------------------------


def input_form():
    """Render the record-entry form and return (subject, record, client)."""

    with st.form("record_form", clear_on_submit=False):
        st.subheader("👤 Subject (candidate being screened)")

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            first = st.text_input("First name*", value="John")
        with c2:
            middle = st.text_input("Middle name", value="Robert")
        with c3:
            last = st.text_input("Last name*", value="Smith")
        with c4:
            suffix = st.text_input("Suffix", value="", disabled=True)

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            dob = st.date_input(
                "DOB*",
                value=date(1985, 6, 15),
                min_value=date(1920, 1, 1),
                max_value=date.today(),
            )
        with c2:
            ssn = st.text_input("SSN last 4", max_chars=4)
        with c3:
            gender_choice = st.selectbox(
                "Gender",
                options=list(GENDER_LABELS.keys()),
                format_func=lambda g: GENDER_LABELS[g],
                index=1,
            )
        with c4:
            race = st.text_input("Race (optional)")

        c1, c2, c3 = st.columns(3)
        with c1:
            salary = st.number_input(
                "Annual salary (for salary-cap states)",
                min_value=0,
                max_value=1_000_000,
                value=60000,
                step=5000,
            )
        with c2:
            name_grade = st.number_input(
                "NameGrade™ score (0–100)",
                min_value=0,
                max_value=100,
                value=45,
                step=1,
                help="From VICTIG's NameGrade tool. ≥58 = common name (needs 3 IDs).",
            )
        with c3:
            addr_states = st.text_input(
                "Prior state history (comma-separated)",
                value="UT",
                help="e.g., UT,CA,NV — states where subject has lived",
            )

        st.subheader("📋 Criminal record")

        c1, c2, c3 = st.columns(3)
        with c1:
            source = st.text_input("Source", value="County Court")
        with c2:
            source_confirmed = st.checkbox(
                "Source confirmed?",
                value=True,
                help="Was this confirmed with the authoritative court/source? "
                "(SOP §2: DB-only hits must be confirmed.)",
            )
        with c3:
            case_number = st.text_input("Case number", value="")

        c1, c2 = st.columns([2, 1])
        with c1:
            charge = st.text_input("Charge description", value="Grand Theft")
        with c2:
            offense_level = st.selectbox(
                "Offense level*",
                options=list(OFFENSE_LEVEL_LABELS.keys()),
                format_func=lambda l: OFFENSE_LEVEL_LABELS[l],
                index=0,
            )

        c1, c2, c3 = st.columns(3)
        with c1:
            state = st.selectbox("State*", options=US_STATES, index=US_STATES.index("UT"))
        with c2:
            county = st.text_input("County", value="Salt Lake")
        with c3:
            disposition = st.selectbox(
                "Disposition*",
                options=list(DISPOSITION_LABELS.keys()),
                format_func=lambda d: DISPOSITION_LABELS[d],
                index=0,
            )

        st.markdown("**Dates**  *(fill in what's available)*")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            arrest_date = st.date_input(
                "Arrest date",
                value=None,
                min_value=date(1970, 1, 1),
                max_value=date.today(),
            )
        with c2:
            file_date = st.date_input(
                "Court file date",
                value=None,
                min_value=date(1970, 1, 1),
                max_value=date.today(),
            )
        with c3:
            disposition_date = st.date_input(
                "Disposition date",
                value=date(2020, 11, 20),
                min_value=date(1970, 1, 1),
                max_value=date.today(),
            )
        with c4:
            release_date = st.date_input(
                "Release date",
                value=None,
                min_value=date(1970, 1, 1),
                max_value=date.today(),
            )

        c1, c2 = st.columns(2)
        with c1:
            parole_date = st.date_input(
                "Parole start date",
                value=None,
                min_value=date(1970, 1, 1),
                max_value=date.today(),
            )
        with c2:
            sentence_max = st.date_input(
                "Max sentence date (if no release date)",
                value=None,
                min_value=date(1970, 1, 1),
                max_value=date.today(),
            )

        st.markdown("**Record-holder identifiers** *(from the court record)*")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            r_first = st.text_input("First (on record)", value="John")
        with c2:
            r_middle = st.text_input("Middle (on record)", value="Robert")
        with c3:
            r_last = st.text_input("Last (on record)", value="Smith")
        with c4:
            r_dob = st.date_input(
                "DOB (on record)",
                value=date(1985, 6, 15),
                min_value=date(1920, 1, 1),
                max_value=date.today(),
            )

        c1, c2, c3 = st.columns(3)
        with c1:
            r_ssn = st.text_input("SSN last 4 (on record)", max_chars=4)
        with c2:
            r_gender = st.selectbox(
                "Gender (on record)",
                options=list(GENDER_LABELS.keys()),
                format_func=lambda g: GENDER_LABELS[g],
                index=1,
                key="rec_gender",
            )
        with c3:
            r_addr_state = st.selectbox(
                "Record location state",
                options=[""] + US_STATES,
                index=(US_STATES.index("UT") + 1),
            )

        st.markdown("**Special flags**")
        c1, c2, c3 = st.columns(3)
        with c1:
            is_mj = st.checkbox(
                "CA non-felony marijuana",
                help="Triggers CA 2-year rule",
            )
        with c2:
            is_amended = st.checkbox(
                "IN felony amended to misdemeanor",
                help="IN Class D/L6 → Class A misdemeanor rule",
            )
        with c3:
            prob_viol_incarc = st.checkbox(
                "Probation violation → incarceration",
                help="SOP §6: active probation doesn't extend timeline, but "
                "a probation-violation incarceration does",
            )

        st.subheader("🏢 Client restrictions (optional)")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            client_name = st.text_input("Client name", value="")
        with c2:
            client_max_misd = st.number_input(
                "Misdemeanor cap (years)",
                min_value=0,
                max_value=30,
                value=0,
                help="0 = use FCRA/state default",
            )
        with c3:
            client_max_fel = st.number_input(
                "Felony cap (years)",
                min_value=0,
                max_value=30,
                value=0,
                help="0 = use FCRA/state default",
            )
        with c4:
            client_felonies_only = st.checkbox("Felonies only")

        submitted = st.form_submit_button(
            "🔍 Analyze",
            type="primary",
            use_container_width=True,
        )

    if not submitted:
        return None

    # Build the models
    addr_states_list = [
        s.strip().upper() for s in addr_states.split(",") if s.strip()
    ]
    address_history = [{"state": s, "county": ""} for s in addr_states_list]

    subject = Subject(
        first_name=first.strip(),
        last_name=last.strip(),
        middle_name=middle.strip() or None,
        dob=dob,
        ssn_last4=ssn.strip() or None,
        gender=gender_choice,
        race=race.strip() or None,
        address_history=address_history,
        annual_salary=float(salary) if salary else None,
        name_grade=int(name_grade),
    )

    record = CriminalRecord(
        record_id=case_number.strip() or "REC-001",
        source=source.strip(),
        source_confirmed=source_confirmed,
        charge_description=charge.strip(),
        offense_level=offense_level,
        disposition=disposition,
        arrest_date=arrest_date if arrest_date else None,
        file_date=file_date if file_date else None,
        disposition_date=disposition_date if disposition_date else None,
        release_date=release_date if release_date else None,
        parole_start_date=parole_date if parole_date else None,
        sentence_max_date=sentence_max if sentence_max else None,
        state=state,
        county=county.strip(),
        case_number=case_number.strip(),
        record_first_name=r_first.strip(),
        record_last_name=r_last.strip(),
        record_middle_name=r_middle.strip() or None,
        record_dob=r_dob if r_dob else None,
        record_ssn_last4=r_ssn.strip() or None,
        record_gender=r_gender,
        record_address_state=r_addr_state or None,
        is_marijuana_possession=is_mj,
        is_amended_from_felony=is_amended,
        is_probation_violation_incarceration=prob_viol_incarc,
    )

    client = None
    if client_name.strip() or client_max_misd or client_max_fel or client_felonies_only:
        client = ClientProfile(
            client_id=client_name.strip().replace(" ", "_")[:32] or "custom",
            client_name=client_name.strip(),
            max_years_misdemeanor=client_max_misd or None,
            max_years_felony=client_max_fel or None,
            felonies_only=client_felonies_only,
        )

    return subject, record, client


# ---------------------------------------------------------------------------
# Sidebar & pages
# ---------------------------------------------------------------------------


def sidebar():
    st.sidebar.title("⚖️ QA Analyzer")
    st.sidebar.caption("VICTIG SOP-driven reportability engine")

    page = st.sidebar.radio(
        "Navigation",
        options=["🔍 Analyze record", "📖 About & SOP references", "📁 State rules"],
        label_visibility="collapsed",
    )
    st.sidebar.divider()

    with st.sidebar:
        st.markdown("### Outcomes")
        st.markdown(
            """
            - <span style='color:#198754;font-weight:600'>REPORT</span>
              — all 4 tests pass, safe to include on report
            - <span style='color:#dc3545;font-weight:600'>EXCLUDE</span>
              — one or more tests definitively fail
            - <span style='color:#ffc107;font-weight:600'>ESCALATE</span>
              — needs human review (missing data, common name, etc.)
            """,
            unsafe_allow_html=True,
        )
        st.divider()
        st.markdown("### The 4 SOP tests")
        st.markdown(
            """
            1. **Reportable offense** (felony/misdemeanor level)
            2. **Within scope** (FCRA + state + client year caps)
            3. **Reportable disposition** (conviction or active pending)
            4. **Sufficient PII** (2+ IDs, or 3+ if common name)
            """
        )
        st.divider()
        st.caption(
            "SOP source: Kate Florez's QA Standard Operating Procedures. "
            "Every rule cites its section for FCRA audit trails."
        )

    return page


def page_analyze():
    st.title("🔍 Analyze a criminal record")
    st.caption(
        "Enter subject + record details. The engine runs the 4 SOP tests + "
        "state law + matching policy and returns REPORT / EXCLUDE / ESCALATE "
        "with a full audit trail."
    )

    result = input_form()
    if result is None:
        return

    subject, record, client = result
    decision = analyze_record(record, subject, client)
    st.divider()
    render_decision(decision, record, subject)

    # Downloadable JSON for audit / integration
    st.divider()
    st.download_button(
        "⬇️ Download decision as JSON (for audit trail)",
        data=json.dumps(decision.to_dict(), indent=2),
        file_name=f"decision_{decision.record_id}.json",
        mime="application/json",
    )


def page_about():
    st.title("📖 About the QA Analyzer")
    st.markdown(
        """
        This tool automates the reportability decision that VICTIG QA
        researchers make on every criminal record. It applies Kate
        Florez's SOP end-to-end and produces an auditable outcome.

        ### What it enforces

        - **SOP §1** — Identity Verification (input requirement)
        - **SOP §2** — NetPlus / source-confirmation requirement
        - **SOP §5** — The four Pending Review tests (offense/scope/disposition/PII)
        - **SOP §6** — Controlling-date calculation (latest of disposition,
          release, parole, or max sentence)
        - **SOP §7** — All state limitations beyond FCRA
        - **SOP §8** — Matching Policy Levels 1/2/3
        - **SOP §18** — Reporting Guidelines (felony/misdemeanor scope,
          MA Ban-the-Box, salary caps)

        ### Precedence when tests disagree

        1. **Any hard-fail → EXCLUDE.** A definitively bad disposition
           (dismissed, expunged) or ineligible offense (traffic infraction)
           excludes the record regardless of identity ambiguity.
        2. **Any escalate flag → ESCALATE.** Missing dates, missing
           NameGrade, common-name + no locational match, unconfirmed
           source — all send to human.
        3. **All pass → REPORT.**

        ### Design principles

        - **Deterministic and auditable.** Every decision returns a
          rule-by-rule reasoning object with SOP citations. No LLM
          black boxes in the critical path.
        - **Data-driven state table.** Update `state_law.py` when Kate
          updates the SOP — no other code changes needed.
        - **Fail safe.** Missing data escalates to human; nothing is
          reported silently.

        ### Known limitations (see `docs/DESIGN.md`)

        - NameGrade is currently an input; automation to come.
        - No connection to VICTIG's record system yet — this is
          reviewer-facing.
        - Missing sections in the SOP (e.g., Missed Records) not yet
          implemented — Kate is still writing them.
        """
    )


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
            cols[0].metric("Conviction cap", f"{rule.conviction_max_years or '—'}y")
            cols[1].metric(
                "Misd cap",
                f"{rule.misdemeanor_max_years or '(same)'}",
            )
            cols[2].metric("Pending cap", f"{rule.pending_max_years or '—'}y")
            cols[3].metric(
                "Salary cap",
                f"${rule.salary_cap:,.0f}" if rule.salary_cap else "—",
            )
            if rule.exclusions:
                st.markdown("**Notes:**")
                for e in rule.exclusions:
                    st.text(f"• {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    page = sidebar()
    if page.startswith("🔍"):
        page_analyze()
    elif page.startswith("📖"):
        page_about()
    else:
        page_state_rules()

    st.markdown(
        "<div class='footer-note'>VICTIG QA Analyzer v0.1 · "
        "SOP source: Kate Florez · "
        "Built for FCRA-compliant reportability review</div>",
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
