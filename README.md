# VICTIG QA Analyzer

Automated criminal-record reportability engine that applies VICTIG's SOP
(FCRA + state law + client restrictions + matching policy) to determine
whether a record can be reported, must be excluded, or needs human review.

**Status:** v0.1 — rules engine, state law table, matching policy, Streamlit UI, 52 unit tests all passing.

## Quick start (local)

```bash
# Install
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Run the web UI (opens on http://localhost:8501)
streamlit run app.py

# Or use the CLI
PYTHONPATH=src python3 -m qa_analyzer.cli analyze examples/sample_case_report.json
PYTHONPATH=src python3 -m qa_analyzer.cli batch examples/sample_batch.json --output results.json

# Run tests
PYTHONPATH=src python3 -m unittest discover tests
```

## Deploy for the team (Streamlit Cloud)

Same pattern as the VICTIG Fraud Detector:

1. Push this repo to GitHub (`lamangamatt/victig-qa-analyzer` or similar)
2. Go to https://share.streamlit.io, connect the repo
3. Deploy `app.py` — auto-deploys on every push to `main`
4. No secrets required for v0.1 (pure deterministic logic, no LLM keys)

## Design goals

1. **Deterministic and auditable.** Every decision returns a full reasoning
   trail suitable for FCRA-compliance audits. No LLM "black boxes" in the
   critical path.
2. **Data-driven.** State limitations, offense-level exclusions, and
   disposition mappings live in editable data files so Kate/QA can update
   rules without touching code.
3. **Fails safe.** When any input is ambiguous or a rule cannot be applied
   with confidence, the record is escalated to human review, never
   silently reported.
4. **Traceable to source.** Every rule cites the SOP page/section it
   implements.

## Structure

- `src/qa_analyzer/models.py` — dataclasses for Subject, Record, Client
- `src/qa_analyzer/rules/` — the four core tests (§5 SOP)
- `src/qa_analyzer/state_law.py` — state limitations table (§7 SOP)
- `src/qa_analyzer/matching.py` — matching policy (§8 SOP)
- `src/qa_analyzer/decision.py` — combined engine
- `src/qa_analyzer/cli.py` — command-line interface
- `examples/` — sample records for testing
- `tests/` — unit tests

## SOP ambiguities resolved (2026-07-24 with Matt)

1. **NameGrade threshold:** Use **58** (per operational SOP §5 test 4). Configurable via `NAMEGRADE_THRESHOLD` constant in `matching.py`.
2. **Mississippi:** Confirmed NOT a strict 7-year state. Removed from the table — falls back to FCRA default.

## Not yet implemented (later phases)

- Integration with VICTIG's actual record data source
- Web UI for operators
- Batch API endpoint
- NameGrade tool integration (requires VICTIG's algorithm access)
- Client preference storage/lookup
