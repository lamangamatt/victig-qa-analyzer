"""LLM-assisted parser: pasted text → structured Subject + CriminalRecord list.

Operators paste whatever they have — court dockets, system exports, plain
text, JSON, CSV, semi-structured logs. The LLM extracts a normalized
structure that matches our engine's schema. The extracted data is
displayed for human verification BEFORE the deterministic analyzer
runs, so the LLM only handles PARSING (variability), never JUDGMENT.

Uses Anthropic Claude. Reads ANTHROPIC_API_KEY from env (Streamlit
Cloud sets this from Streamlit Secrets).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import date
from typing import Optional

from qa_analyzer.models import (
    CriminalRecord,
    Disposition,
    Gender,
    OffenseLevel,
    Subject,
)


DEFAULT_MODEL = "claude-sonnet-4-5"


# ---------------------------------------------------------------------------
# Schema description for the LLM (short, precise)
# ---------------------------------------------------------------------------

SCHEMA_PROMPT = """You extract structured criminal-record data from operator-pasted text.

Output STRICT JSON matching this schema (no prose, no markdown):

{
  "subject": {
    "first_name": string,
    "last_name": string,
    "middle_name": string | null,
    "dob": "YYYY-MM-DD" | null,
    "ssn_last4": "1234" | null,
    "gender": "male" | "female" | null,
    "race": string | null,
    "annual_salary": number | null,
    "name_grade": integer | null,     // 0-100, VICTIG NameGrade score
    "address_history": [{ "state": "XX", "county": string | null }]
  },
  "records": [
    {
      "record_id": string,             // case number or a generated id
      "source": string,                // e.g. "County Court", "NetPlus", "PACER"
      "source_confirmed": boolean,     // true unless it clearly says DB-only
      "charge_description": string,
      "offense_level": "felony" | "misdemeanor" | "traffic_infraction" |
                       "ordinance" | "petty_misdemeanor" | "minor_misdemeanor" |
                       "summary_offense" | "petty_disorderly" | "forfeiture_u" |
                       "unknown",
      "disposition": "convicted" | "guilty" | "nolo_contendere" |
                     "pending" | "deferred" | "diversion" | "first_offender" |
                     "dismissed" | "acquitted" | "not_guilty" |
                     "adjudication_withheld" | "deferred_completed" |
                     "expunged" | "sealed" | "juvenile" | "arrest_only" |
                     "no_disposition" | "unknown",
      "arrest_date": "YYYY-MM-DD" | null,
      "file_date": "YYYY-MM-DD" | null,
      "disposition_date": "YYYY-MM-DD" | null,
      "release_date": "YYYY-MM-DD" | null,
      "parole_start_date": "YYYY-MM-DD" | null,
      "sentence_max_date": "YYYY-MM-DD" | null,
      "state": "XX",                    // 2-letter state code
      "county": string,
      "court_name": string,
      "case_number": string,
      "record_first_name": string,
      "record_last_name": string,
      "record_middle_name": string | null,
      "record_dob": "YYYY-MM-DD" | null,
      "record_ssn_last4": "1234" | null,
      "record_gender": "male" | "female" | null,
      "record_address_state": "XX" | null,
      "is_marijuana_possession": boolean,
      "is_amended_from_felony": boolean,
      "is_probation_violation_incarceration": boolean
    }
  ],
  "client": {
    "client_id": string | null,
    "client_name": string | null,
    "max_years_misdemeanor": integer | null,
    "max_years_felony": integer | null,
    "felonies_only": boolean,
    "sex_offenses_only": boolean
  } | null,
  "notes": [string],                    // ambiguities, missing fields, warnings
  "confidence": "high" | "medium" | "low",
  "confidence_reason": string           // 1-2 sentences explaining WHY
}

RULES:
- Use "unknown" for offense_level or disposition ONLY if truly unclear.
  Map common terms:
    "guilty", "convicted", "conviction" → "convicted"
    "no contest", "nolo" → "nolo_contendere"
    "dismissed", "dismissal" → "dismissed"
    "not guilty", "acquitted", "NG" → "acquitted"
    "pending", "open", "active" → "pending"
    "PBJ", "probation before judgment" → "deferred"
    "expunged", "expunction" → "expunged"
    "sealed", "under seal" → "sealed"
    "juvenile", "juv" → "juvenile"
    "adjudication withheld", "adj withheld", "AW" → "adjudication_withheld"
    "diversion completed" → "deferred_completed"
    "diversion in progress", "active diversion" → "diversion"
    "arrest only", "no charges filed" → "arrest_only"
- For offense_level: F/felony → "felony"; M/misdemeanor → "misdemeanor";
  infraction/citation/violation → "traffic_infraction" ONLY if clearly
  traffic. Otherwise "unknown".
- Extract multiple records if present. Each record on its own.
- Use record_first_name/record_last_name from the CHARGE record, not the
  candidate. If not separately listed, copy subject's name.
- If SSN is shown, extract only the LAST 4 digits.
- source_confirmed=true unless the text explicitly says "database only",
  "unverified", "pointer only", or similar.
- Set is_marijuana_possession=true ONLY for CA non-felony marijuana
  possession charges.
- Add every unclear/missing-but-needed field to "notes".
- If no candidate info at all, still return the JSON with best guesses
  in "notes".

CONFIDENCE RUBRIC:
- "high": ALL required fields are clearly present in the source text
  with no ambiguity. Offense level, disposition, jurisdiction, all key
  dates, and identity fields all cleanly extractable. No enum mapping
  guesswork required.
- "medium": Most fields present, but at least one of:
    (a) An offense level or disposition was INFERRED from ambiguous
        language (e.g., only a charge description, no explicit F/M);
    (b) A key date is missing (disposition date, arrest date, etc.)
        but was reconstructable from context;
    (c) Record-holder identity fields (record_first_name/last_name)
        were assumed to match the candidate because not separately listed;
    (d) Any state-specific special handling (marijuana, amended felony,
        etc.) was best-guessed.
- "low": Multiple critical fields missing or highly ambiguous:
    disposition is "no_disposition" or "unknown", offense_level is
    "unknown", jurisdiction is missing, or key dates absent AND
    unrecoverable. This paste needs heavy operator review.

Always populate "confidence_reason" with a 1-2 sentence explanation
calling out the SPECIFIC fields that drove the rating (e.g. "Offense
  level inferred from 'Theft F3' as felony; disposition_date reconstructed
  from sentencing paragraph.")

Return ONLY the JSON object. No preamble, no explanation.
"""


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ParserError(Exception):
    """Raised when parsing fails for a reason the user should see."""


class ParserUnavailable(ParserError):
    """No API key configured; parser can't run."""


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def is_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def parse(text: str, model: str = DEFAULT_MODEL) -> dict:
    """Extract structured data from pasted text.

    Returns a dict with keys: subject, records, client, notes, confidence.
    Raises ParserError on failure with a user-friendly message.
    """
    if not text.strip():
        raise ParserError("Empty input. Paste some data and try again.")

    if not is_available():
        raise ParserUnavailable(
            "Parser needs ANTHROPIC_API_KEY. Add it in Streamlit Cloud "
            "app settings → Secrets, then reboot the app."
        )

    try:
        import anthropic
    except ImportError:
        raise ParserError(
            "The 'anthropic' package is not installed. Add it to "
            "requirements.txt and redeploy."
        )

    client = anthropic.Anthropic()

    # Use tool_use to force valid, complete JSON output. This eliminates
    # the truncation risk that comes with free-form JSON generation.
    tool = {
        "name": "emit_parsed_case",
        "description": "Emit the parsed candidate + records data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "object"},
                "records": {"type": "array", "items": {"type": "object"}},
                "client": {"type": ["object", "null"]},
                "notes": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "string",
                               "enum": ["high", "medium", "low"]},
            },
            "required": ["subject", "records"],
        },
    }

    try:
        response = client.messages.create(
            model=model,
            max_tokens=16384,
            temperature=0,
            system=SCHEMA_PROMPT,
            tools=[tool],
            tool_choice={"type": "tool", "name": "emit_parsed_case"},
            messages=[{"role": "user", "content": text}],
        )
    except Exception as e:
        raise ParserError(f"LLM call failed: {type(e).__name__}: {e}") from e

    if not response.content:
        raise ParserError("LLM returned empty response.")

    # Find the tool_use block in the response
    tool_use_block = None
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            tool_use_block = block
            break

    if tool_use_block is None:
        # Fallback: try free-form text parsing (older-model behavior)
        raw = response.content[0].text.strip() if hasattr(response.content[0], "text") else ""
        if raw.startswith("```"):
            lines = raw.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            raw = "\n".join(lines).strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            hint = (
                " (response may be truncated; try splitting into smaller pastes)"
                if len(raw) > 10000
                else ""
            )
            raise ParserError(
                f"LLM returned non-JSON output{hint}. First 400 chars:\n"
                f"{raw[:400]}\n\nError: {e}"
            )
    else:
        # tool_use returns parsed dict directly — no JSON decode step needed
        parsed = tool_use_block.input

    # Check for truncation via stop_reason
    if getattr(response, "stop_reason", None) == "max_tokens":
        raise ParserError(
            "Response was truncated (hit max_tokens). Try splitting your "
            "paste into fewer records at a time."
        )

    # Basic schema sanity
    if "subject" not in parsed:
        raise ParserError("Parsed output missing 'subject'.")
    if "records" not in parsed or not isinstance(parsed["records"], list):
        raise ParserError("Parsed output missing 'records' list.")

    parsed.setdefault("notes", [])
    parsed.setdefault("confidence", "medium")
    parsed.setdefault("confidence_reason", "")
    parsed.setdefault("client", None)
    return parsed


# ---------------------------------------------------------------------------
# Dict → model conversion (with safe defaults)
# ---------------------------------------------------------------------------


def _parse_date(v) -> Optional[date]:
    if not v:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(v)
    except (ValueError, TypeError):
        return None


def _to_enum(cls, v, default):
    if v is None:
        return default
    try:
        return cls(v)
    except ValueError:
        return default


def dict_to_subject(d: dict) -> Subject:
    return Subject(
        first_name=(d.get("first_name") or "").strip(),
        last_name=(d.get("last_name") or "").strip(),
        middle_name=(d.get("middle_name") or None),
        dob=_parse_date(d.get("dob")),
        ssn_last4=(d.get("ssn_last4") or None),
        gender=_to_enum(Gender, d.get("gender"), None),
        race=(d.get("race") or None),
        address_history=d.get("address_history") or [],
        annual_salary=d.get("annual_salary"),
        name_grade=d.get("name_grade"),
    )


def dict_to_record(d: dict) -> CriminalRecord:
    return CriminalRecord(
        record_id=(d.get("record_id") or d.get("case_number") or "REC-001"),
        source=(d.get("source") or ""),
        source_confirmed=bool(d.get("source_confirmed", True)),
        charge_description=(d.get("charge_description") or ""),
        offense_level=_to_enum(OffenseLevel, d.get("offense_level"), OffenseLevel.UNKNOWN),
        disposition=_to_enum(Disposition, d.get("disposition"), Disposition.UNKNOWN),
        arrest_date=_parse_date(d.get("arrest_date")),
        file_date=_parse_date(d.get("file_date")),
        disposition_date=_parse_date(d.get("disposition_date")),
        release_date=_parse_date(d.get("release_date")),
        parole_start_date=_parse_date(d.get("parole_start_date")),
        sentence_max_date=_parse_date(d.get("sentence_max_date")),
        state=(d.get("state") or "").upper(),
        county=(d.get("county") or ""),
        court_name=(d.get("court_name") or ""),
        case_number=(d.get("case_number") or ""),
        record_first_name=(d.get("record_first_name") or ""),
        record_last_name=(d.get("record_last_name") or ""),
        record_middle_name=(d.get("record_middle_name") or None),
        record_dob=_parse_date(d.get("record_dob")),
        record_ssn_last4=(d.get("record_ssn_last4") or None),
        record_gender=_to_enum(Gender, d.get("record_gender"), None),
        record_address_state=(d.get("record_address_state") or None),
        is_marijuana_possession=bool(d.get("is_marijuana_possession", False)),
        is_amended_from_felony=bool(d.get("is_amended_from_felony", False)),
        is_probation_violation_incarceration=bool(
            d.get("is_probation_violation_incarceration", False)
        ),
    )


def dict_to_client(d: Optional[dict]):
    if not d:
        return None
    from qa_analyzer.models import ClientProfile
    if not any(d.get(k) for k in (
        "client_name", "client_id", "max_years_misdemeanor",
        "max_years_felony", "felonies_only", "sex_offenses_only",
    )):
        return None
    return ClientProfile(
        client_id=(d.get("client_id") or ""),
        client_name=(d.get("client_name") or ""),
        max_years_misdemeanor=d.get("max_years_misdemeanor"),
        max_years_felony=d.get("max_years_felony"),
        felonies_only=bool(d.get("felonies_only", False)),
        sex_offenses_only=bool(d.get("sex_offenses_only", False)),
    )
