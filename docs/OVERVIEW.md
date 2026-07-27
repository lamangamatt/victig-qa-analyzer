# VICTIG QA Analyzer — What It Does & How Data Is Handled

*A shareable description for QA staff, legal review, and client compliance
questions. Reflects the app as deployed at
`https://matts-mac-mini.tailefa08d.ts.net/qa-analyzer/` (v0.3, July 2026).*

---

## What it is

An internal, SOP-driven tool that automates the reportability decision a QA
researcher makes on every criminal record. Given a candidate + one or more
criminal records, it returns **REPORT / EXCLUDE / ESCALATE** with a full
audit trail citing the exact SOP section that drove each conclusion.

The rules engine is a direct implementation of Kate Florez's QA SOP. The LLM
only structures the paste text so the SOP engine can apply its rules;
**every reportability decision is deterministic and auditable.**

## What it does

For each criminal record, the analyzer applies:

1. **SOP §5 — Four core tests**
   - Reportable offense (felony / misdemeanor)
   - Within scope (FCRA + state law + client caps)
   - Reportable disposition (conviction or active pending)
   - Sufficient PII match (2 identifiers, or 3 if common name)
2. **SOP §6 — Controlling-date calculation** (which date starts the clock)
3. **SOP §7 — 19 states with special rules** (7-year states, salary caps, etc.)
4. **SOP §8 — Matching Policy** (Level 1/2/3 with NameGrade threshold 58)
5. **SOP §18 — Reporting guidelines** (Ban-the-Box, salary caps)

Precedence: any hard-fail → **EXCLUDE**. Any escalate flag → **ESCALATE**.
All pass → **REPORT**. Missing data always → **ESCALATE**, never a silent
report.

## How data is handled

The analyzer is hosted on VICTIG's Mac mini, gated behind a shared access
token, and reached over Tailscale Funnel with TLS. It is not on Streamlit
Cloud or any third-party host. There is no persistent database of
candidates or records — data lives only in the operator's browser session
while they use the tool.

### PII shield (privacy pipeline)

Before any paste is sent to Anthropic Claude for structural parsing, the
following identifiers are **stripped on the Mac mini and replaced with
realistic-looking pseudonyms**:

- Names (candidate, defendant, co-defendant, victim, applicant, etc.)
- Dates of birth (in any format)
- Social Security Numbers (full or partial)
- Street addresses (street number + name)
- Phone numbers
- Email addresses
- Case numbers / docket numbers

The pseudonymization is consistent: the same real value maps to the same
fake across the entire paste, so a person mentioned three times still
looks like one person to the parser. After Claude returns structured JSON,
the real values are substituted back locally on the Mac mini. **The real
PII never leaves VICTIG-controlled infrastructure.**

### Concrete example

Where the raw paste says:
```
Name: John Robert Smith
DOB: 06/15/1985
SSN: XXX-XX-1234
Address: 123 Main St, Salt Lake City, UT 84101
Case Number: 201234567
```

Anthropic Claude receives:
```
Name: Aria A Ashford
DOB: 1900-01-01
SSN: XXX-XX-0001
Address: 101 Placeholder Street, Salt Lake City, UT 84101
Case Number: CASE-000001
```

The charge, offense level, disposition, dates, court, jurisdiction, and
all other structural fields pass through unchanged so the SOP engine has
everything it needs.

### What is retained where

- **VICTIG (Mac mini):** local SQLite audit log with timestamp, SHA-256
  hash of the original input, redaction counts by category, model name,
  token counts, latency, and status. **No PII, no raw input text, no
  response text.**
- **Anthropic API:** default 30-day server-side retention for safety
  review, no use for training. Because the input is pseudonymized before
  it arrives, any retained content is structurally intact but personally
  anonymous.
- **Zero-Data-Retention (ZDR) request** is on file with Anthropic. Once
  ZDR is approved, retention will drop to zero on their side as well; the
  redaction pipeline will remain as a defense-in-depth layer regardless.

### Access controls

- Shared access token (32-byte hex) required for entry, either via URL
  or manual login
- Served over TLS (Tailscale Funnel + Let's Encrypt)
- Token stored on the Mac mini under `~/.qa-analyzer.env`, chmod 600
- Audit database chmod 600, human-readable via the "Audit log" page in
  the UI

## Compliance posture

- **FCRA sub-processor documentation:** the local audit log is the
  required record of every data transmission to Anthropic. It proves
  what was sent (by hash) and when, without duplicating the PII.
- **No training use:** default under Anthropic's API terms.
- **Human-in-the-loop:** the parser structures text, but a QA researcher
  reviews the extracted fields before the deterministic engine runs.
  Every rule outcome cites its SOP section for review.
- **No persistent record store on our side beyond the log:** no candidate
  database, no record cache.

## Validation

The redaction pipeline is validated by a test suite that runs the same
paste through the parser twice — once with PII stripping enabled, once
without — and diffs the semantically important fields
(charge, offense level, disposition, dates, jurisdiction, identity). All
current test cases produce identical output on both paths, confirming
that redaction does not degrade parse quality.

See:
- `tests/test_redaction.py` — 24 unit tests covering redaction of every
  PII category
- `tests/test_audit.py` — 9 tests covering the local audit log
- `scripts/validate_redaction.py` — end-to-end diff harness against 6
  synthetic paste formats

## Current status

- Live at https://matts-mac-mini.tailefa08d.ts.net/qa-analyzer/
- Access by token only; contact Matt Visser for credentials
- Version 0.3 (July 2026): PII shield + audit log added
- Anthropic ZDR request submitted; awaiting reply

---

## Change log

- **v0.3** (2026-07-27) — Added PII shield (pre-send pseudonymization) and
  local audit log. Migrated off Streamlit Cloud to VICTIG-controlled
  hosting.
- **v0.2** (2026-07-24) — Streamlit UI, per-record decisions, Matching
  Policy detail, downloadable audit JSON.
- **v0.1** (2026-07-24) — Initial engine: 4 SOP tests, state law table,
  matching policy, CLI, 52 unit tests.
