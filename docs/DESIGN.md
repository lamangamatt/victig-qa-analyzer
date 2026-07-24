# QA Analyzer — Design & Status

## What this program does

Automates the reportability decision that a VICTIG QA researcher makes
on every criminal record: given (1) a subject, (2) a criminal record from
some source (NetPlus, county court, PACER, etc.), and (3) an optional
client profile, determine whether the record should be:

- **REPORT** — passes all SOP tests, safe to include on the consumer report
- **EXCLUDE** — definitively fails one or more SOP tests, must be removed
- **ESCALATE** — has ambiguity/missing data that requires human review

Every decision comes with a full **audit trail** citing exact SOP sections,
so QA can defend every reportability call to the FCRA / consumer bureaus.

## What's implemented (v0.1)

### Core rules engine

- **Test 1 — Reportable Offense Level** (SOP §5 test 1, §18)
  - Felony/misdemeanor pass; traffic infractions, ordinances, WI
    Forfeiture U, PA Summary Offenses, NJ Petty Disorderly all fail.
  - Handles Indiana Class D/Level 6 → Class A Misdemeanor amendment rule.

- **Test 2 — Within Scope of Reporting** (SOP §5 test 2, §6, §7)
  - Computes controlling date per §6 (latest of disposition / release /
    parole / max sentence — active probation does NOT extend).
  - Applies FCRA (10yr misdemeanor, indefinite felony) as baseline.
  - Overrides with state rules (see State Law Table below).
  - Applies salary-cap logic where applicable.
  - Applies client-imposed max-year overrides.
  - Applies non-conviction 7-yr-from-arrest rule.

- **Test 3 — Reportable Disposition** (SOP §5 test 3)
  - Convictions & nolo → pass.
  - Active pending/deferred/diversion/first-offender → pass (report as
    active non-conviction under 7-yr rule).
  - Dismissed / acquitted / adjudication-withheld / deferred-completed /
    expunged / sealed / juvenile / arrest-only → hard-fail.
  - Missing disposition → SOP §2 rule: "assume conviction, follow up" →
    ESCALATE for research team.

- **Test 4 — Sufficient PII to Match** (SOP §5 test 4, §8)
  - Runs Matching Policy Level 1/2/3.
  - Uses NameGrade threshold to demand 2 vs. 3 identifiers.
  - Level 2 disqualifier (gender/race mismatch when both present) →
    hard-fail.
  - Level 3 common-name + no locational match → ESCALATE for ≥2
    supervisor approval (SOP §8).
  - Other L3 flags reported as informational warnings only.

### State Law Table (data-driven, `state_law.py`)

Currently coded from SOP §7 (p.14–15) + §18 (p.18):

| State | Rule |
|---|---|
| AK | Conviction only (pending never reportable) |
| AR | Pending 3yr from arrest |
| CA | Convictions 7yr; non-felony marijuana 2yr max; deferrals excluded if completed |
| DC | Convictions 10yr from sentence completion |
| HI | Felony 7yr / misdemeanor 5yr, DISPOSITION-only (no incarceration time) |
| ID | Pending 1yr unless written consent |
| IL | Adjudication-withheld/deferred excluded if no other conviction on report |
| IN | Class D/L6 felony amended to misdemeanor: report misdemeanor form only |
| KS | Convictions 7yr if salary < $20k/yr |
| KY | Conviction only |
| LA | Traffic-failure-to-pay: 120-day quarantine post-disposition |
| MD | Convictions 7yr if salary < $75k/yr |
| MA | Felony 7yr, Misdemeanor 3yr (Ban The Box) |
| MS | ⚠️ Listed on p.18 as 7yr state but missing from §7 table (confirm w/ Kate) |
| MT | Convictions 7yr |
| NE | No access to arrest information |
| NH | Convictions 7yr if salary < $20k/yr |
| NM | Convictions 7yr |
| NY | Convictions 7yr if salary < $25k/yr; pending reportable |
| WA | Convictions 7yr if salary < $20k/yr |

### Cross-cutting rules

- **Source confirmation** (SOP §2): unconfirmed source triggers ESCALATE
  when all other tests pass — enforces "confirm with source before reporting."
- **Arrest-only** records: never reportable (EEOC).
- **California marijuana 2-year rule** applied as post-check.
- **Illinois adjudication-withheld** rule applied as post-check.

### Output

Structured `Decision` object with:
- `outcome`: REPORT / EXCLUDE / ESCALATE
- `rule_results`: full per-test reasoning with SOP citation
- `matching_score`: Level 1/2/3 detail
- `state_rules_applied`: state law notes shown
- `controlling_date` + reason: for FCRA transparency
- `warnings` and `escalation_reasons`

Both JSON (for API/integration) and colored terminal output (for CLI use).

### CLI

```bash
# One case with pretty output
python3 -m qa_analyzer.cli analyze examples/sample_case_report.json

# One case, JSON output for piping/integration
python3 -m qa_analyzer.cli analyze examples/sample_case_report.json --json

# Batch mode
python3 -m qa_analyzer.cli batch examples/sample_batch.json --output results.json
```

### Tests

52 unit tests covering: each of the 4 tests, every implemented state
rule, cross-cutting rules, matching policy edge cases, precedence,
and client-restriction overrides. All passing.

## Precedence rule (important)

When tests disagree:

1. **Any hard-fail** (definitive "no") → **EXCLUDE**
2. Otherwise, any escalate flag → **ESCALATE**
3. Otherwise, all pass → **REPORT**

This means a record with a known-bad disposition (dismissed) is EXCLUDED
even if the identity match is ambiguous — we don't need to know who the
record belongs to before deciding it can't be reported.

## Known SOP ambiguities (flagged for Kate)

1. **NameGrade threshold** — p.11 says "58 or above", p.17 says "above
   56". Currently using **58** per operational SOP. Configurable in
   `matching.py` (`NAMEGRADE_THRESHOLD`).

2. **Mississippi** — Listed on p.18 as strict 7-year state but not in
   §7 table. Currently treated as 7-year with an inline warning. Kate
   should either add MS to §7 or remove from §18.

3. **Disposition Jargon spreadsheet** — SOP §5 test 3 references this
   external doc. My `Disposition` enum uses standard names; if VICTIG
   has state-specific jargon (e.g., WA "SIS", MO "SES"), we should map
   those to the enum via a separate spreadsheet.

## What's NOT yet implemented

These are the next steps once the current design is validated:

- [ ] **Data ingestion adapters** — plug into VICTIG's actual record
      system. Need to see the export format (CSV? JSON? DB?) to build
      the adapter.
- [ ] **NameGrade tool integration** — currently accepts NameGrade
      as an input field. Need VICTIG's algorithm/API access to compute
      it in-line.
- [ ] **Client profile storage** — currently accepts inline JSON. Should
      pull from VICTIG's client-preferences database.
- [ ] **Web UI for operators** — a simple Streamlit or React interface
      so operators can paste a record and get an instant decision with
      reasoning.
- [ ] **Batch pipeline** — hook up to whatever queueing system feeds QA
      today so decisions are computed automatically.
- [ ] **Sex Offender Registry check** — SOP §2 mentions positive matches.
      Currently a data field but no automation.
- [ ] **BIS / OIG / SAM registry checks** — same as above.
- [ ] **Missed-Records section** — Kate is still writing this. Watch for
      that update and code it once available.
- [ ] **Disposition-jargon lookup table** — map state-specific dispo
      wording (e.g., "SIS", "SES", "PTI") to canonical Disposition enum.
- [ ] **Age-of-consent / juvenile / DOC handling** for records where DOB
      is 17-and-under at offense (currently just a Disposition value).
- [ ] **Address history parser** — currently accepts structured list;
      should parse VICTIG's actual address-history format.

## Architecture principles

1. **Deterministic first, LLM optional.** Every rule in the SOP is
   deterministic — coded as data + logic, not prompted. If we ever add
   LLM-assisted analysis for edge cases (e.g., interpreting messy court
   text), it must be optional and only after deterministic tests run.
2. **Data-driven state rules.** State limitations live in one dict in
   `state_law.py`. Kate/QA can update rules without touching business
   logic.
3. **Full audit trail always.** Every decision returns a rule-by-rule
   reasoning object suitable for FCRA compliance review.
4. **Fail safe.** Missing data → ESCALATE, not silent report. It is
   always safer to over-escalate than to under-report a disqualifier.
5. **Independent of VICTIG's stack.** Pure Python stdlib. Can be
   dropped into Streamlit, FastAPI, a queue worker, or a CLI.

## File map

```
qa-analyzer/
├── README.md                       # Overview
├── docs/DESIGN.md                  # This file
├── src/qa_analyzer/
│   ├── __init__.py
│   ├── models.py                   # dataclasses (Subject, Record, etc.)
│   ├── state_law.py                # state limitations table
│   ├── matching.py                 # Levels 1/2/3 matching policy
│   ├── decision.py                 # top-level engine (analyze_record)
│   ├── cli.py                      # command-line interface
│   └── rules/
│       ├── __init__.py
│       ├── test1_offense.py        # offense-level test
│       ├── test2_scope.py          # scope + state law test
│       ├── test3_disposition.py    # disposition test
│       └── test4_pii.py            # PII match test
├── examples/
│   ├── README.md                   # JSON schema
│   ├── sample_case_*.json          # individual test cases
│   └── sample_batch.json           # multi-case batch
└── tests/
    └── test_engine.py              # 52 unit tests
```
