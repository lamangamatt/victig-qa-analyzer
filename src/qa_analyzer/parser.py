"""LLM-assisted parser: pasted text → structured Subject + CriminalRecord list.

Operators paste whatever they have — court dockets, system exports, plain
text, JSON, CSV, semi-structured logs. The LLM extracts a normalized
structure that matches our engine's schema. The extracted data is
displayed for human verification BEFORE the deterministic analyzer
runs, so the LLM only handles PARSING (variability), never JUDGMENT.

Data-protection layer (v0.3+): before sending the paste to Claude, we
run `redaction.redact()` to swap all PII (names, DOB, SSN, addresses,
phone/email, case numbers) for realistic-looking pseudonyms. Claude
parses the pseudonymized paste; then we substitute the real values
back into the returned JSON locally. Every call is logged to a local
SQLite audit table (no PII in the log). Set redact=False only for
unit tests or synthetic-data validation.

Uses Anthropic Claude. Reads ANTHROPIC_API_KEY from env.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from datetime import date
from typing import Optional

from qa_analyzer import audit, redaction
from qa_analyzer.models import (
    CriminalRecord,
    Disposition,
    Gender,
    OffenseLevel,
    Subject,
)


# Max output tokens per LLM call. 16384 is the effective cap for Haiku
# 4.5 without streaming (32K+ requires the streaming API). Empirically
# this fits ~25-30 records worth of structured JSON output; beyond that,
# we auto-chunk the paste by record boundary.
MAX_TOKENS_PER_CALL = 16384


# Claude Haiku 4.5 is 3-5x faster and ~10x cheaper than Sonnet, with
# essentially identical accuracy for structured extraction (parsing is
# not a judgment task — the deterministic engine does all judgment).
DEFAULT_MODEL = "claude-haiku-4-5"


# ---------------------------------------------------------------------------
# Schema description for the LLM (short, precise)
# ---------------------------------------------------------------------------

SCHEMA_PROMPT = """You extract structured criminal-record data from operator-pasted text.

PRIVACY NOTE: The paste you receive may have been PSEUDONYMIZED before
you see it — identifiers like names, dates of birth, SSNs, addresses,
phones, emails, and case numbers may be replaced with realistic-
looking placeholders such as "Aria Ashford", "1900-01-01",
"XXX-XX-0001", "555-000-0001", or "CASE-000001". These substitutions
are intentional privacy controls on our side. Do NOT flag them as
suspicious, placeholder, test data, or missing. Treat them as normal
data and extract them exactly as they appear. The real values will be
restored downstream after your response.

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


def parse(
    text: str,
    model: str = DEFAULT_MODEL,
    redact: bool = True,
    log: bool = True,
) -> dict:
    """Extract structured data from pasted text.

    Args:
        text: raw paste from the operator.
        model: Claude model name (default Haiku).
        redact: if True (default), strip PII before sending to Claude
            and substitute back locally. Set False only for unit tests
            or when running against synthetic data.
        log: if True (default), record the call in the local audit
            SQLite table.

    Returns:
        Dict with keys: subject, records, client, notes, confidence,
        confidence_reason, and (when redact=True) pii_stats.

    Raises:
        ParserError on failure with a user-friendly message.
    """
    if not text.strip():
        raise ParserError("Empty input. Paste some data and try again.")

    if not is_available():
        raise ParserUnavailable(
            "Parser needs ANTHROPIC_API_KEY. Set it in the environment "
            "(dev) or ~/.qa-analyzer.env (Mac mini deployment)."
        )

    try:
        import anthropic
    except ImportError:
        raise ParserError(
            "The 'anthropic' package is not installed. Add it to "
            "requirements.txt and redeploy."
        )

    # --- Redact PII before send -----------------------------------------
    if redact:
        redacted_text, pii_map = redaction.redact(text)
    else:
        redacted_text, pii_map = text, redaction.PIIMap()

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

    timer = audit.Timer()
    log_event = None
    tokens_in = None
    tokens_out = None

    try:
        with timer:
            response = client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS_PER_CALL,
                temperature=0,
                system=SCHEMA_PROMPT,
                tools=[tool],
                tool_choice={"type": "tool", "name": "emit_parsed_case"},
                messages=[{"role": "user", "content": redacted_text}],
            )
    except Exception as e:
        # Log the failure before raising
        if log:
            _log_safe(audit.ParseEvent(
                input_text=text,
                redacted_text=redacted_text,
                redaction_stats=pii_map.stats(),
                model=model,
                latency_ms=getattr(timer, "elapsed_ms", 0),
                status="error",
                error_message=f"{type(e).__name__}: {e}",
            ))
        raise ParserError(f"LLM call failed: {type(e).__name__}: {e}") from e

    try:
        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
    except AttributeError:
        pass

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

    # Check for truncation via stop_reason. If we hit max_tokens even at
    # our high per-call limit, fall back to auto-chunking the paste by
    # record boundary and parsing each chunk separately.
    if getattr(response, "stop_reason", None) == "max_tokens":
        chunks = _split_by_record_boundary(redacted_text)
        if len(chunks) > 1:
            merged = _parse_and_merge_chunks(
                chunks, client, tool, model, timer,
            )
            parsed = merged
            # Re-derive token counts from merged.
            try:
                tokens_in = None  # sum tracked inside merge helper if desired
                tokens_out = None
            except AttributeError:
                pass
        else:
            raise ParserError(
                "Response was truncated (hit max_tokens) and the paste "
                "could not be automatically split into smaller chunks. "
                "Try splitting your paste by record and re-running."
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

    # --- Substitute real values back in ---------------------------------
    if redact:
        parsed = pii_map.substitute_parsed(parsed)
        parsed["pii_stats"] = pii_map.stats()

    # --- Audit log (success path) ---------------------------------------
    if log:
        _log_safe(audit.ParseEvent(
            input_text=text,
            redacted_text=redacted_text,
            redaction_stats=pii_map.stats(),
            model=model,
            latency_ms=timer.elapsed_ms,
            status="ok",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            parse_confidence=parsed.get("confidence"),
            record_count=len(parsed.get("records") or []),
        ))

    return parsed


def _log_safe(event: audit.ParseEvent) -> None:
    """Write to audit log; never let a logging failure break the parse."""
    try:
        audit.default_log().log(event)
    except Exception:  # noqa: BLE001 — audit failure should never surface
        pass


# ---------------------------------------------------------------------------
# Auto-chunking (fallback when a single call hits max_tokens)
# ---------------------------------------------------------------------------

# Regex candidates for detecting where a new criminal record starts.
# Ranked from STRONGEST (highest confidence per-hit is a real record
# boundary) to WEAKEST (more likely to false-fire). We pick the
# strongest pattern that has ≥2 hits in the text and use ONLY that.
_RECORD_HEADER_PATTERNS = [
    # Numbered records: "Record 1:", "Record #2", "RECORD 3"
    re.compile(r"^\s*(?:RECORD|Record)\s*[#\d]", re.MULTILINE),
    # Numbered cases: "Case 1:", "CASE #2:", "CASE 3"
    re.compile(r"^\s*(?:CASE|Case)\s+[#\d]", re.MULTILINE),
    # Header lines like "CRIMINAL RECORD #1" or "CRIMINAL RECORD:"
    re.compile(r"^\s*CRIMINAL\s+RECORD\b", re.MULTILINE),
    # Case number labels — weakest: assumes exactly one per record
    re.compile(
        r"^\s*(?:Case\s+Number|Case\s+No\.?|Docket(?:\s+No\.?|\s+#)?)\s*[:\-]",
        re.MULTILINE | re.IGNORECASE,
    ),
]


def _split_by_record_boundary(text: str) -> list[str]:
    """Split a paste into (candidate + one_record) chunks.

    Returns a list of chunk strings, each of which includes the
    candidate header (so every chunk is a self-contained parseable
    unit). If no reliable record boundaries can be found, returns
    a single-element list containing the whole text.

    Algorithm:
        1. Try each pattern in order of strength.
        2. First pattern with ≥2 hits wins — use ONLY that pattern's
           positions as record boundaries.
        3. Everything before the first boundary = candidate header,
           prepended to every chunk.
    """
    if not text or not text.strip():
        return [text]

    starts: list[int] = []
    for pat in _RECORD_HEADER_PATTERNS:
        hits = [m.start() for m in pat.finditer(text)]
        if len(hits) >= 2:
            starts = sorted(hits)
            break

    if len(starts) < 2:
        return [text]

    first_start = starts[0]
    if first_start == 0:
        # No candidate header. Chunks are the record segments as-is.
        header = ""
    else:
        header = text[:first_start]

    starts.append(len(text))
    chunks: list[str] = []
    for i in range(len(starts) - 1):
        piece = text[starts[i]:starts[i + 1]].rstrip() + "\n"
        chunks.append((header + piece) if header else piece)

    return chunks


def _parse_and_merge_chunks(
    chunks: list[str],
    client,
    tool: dict,
    model: str,
    timer,
) -> dict:
    """Parse each chunk separately and merge into a single result.

    Merging strategy:
        - subject / client: take from the first chunk that has them.
        - records: concatenate across chunks (preserving order).
        - notes: union across chunks + a note about auto-chunking.
        - confidence: worst-case across chunks.
    """
    all_parsed: list[dict] = []
    for chunk in chunks:
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS_PER_CALL,
            temperature=0,
            system=SCHEMA_PROMPT,
            tools=[tool],
            tool_choice={"type": "tool", "name": "emit_parsed_case"},
            messages=[{"role": "user", "content": chunk}],
        )
        # Extract tool_use
        tool_block = None
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                tool_block = block
                break
        if tool_block is None:
            raise ParserError(
                "Auto-chunk parse failed to return structured data. "
                "Try splitting the paste manually into fewer records."
            )
        p = dict(tool_block.input)
        if getattr(response, "stop_reason", None) == "max_tokens":
            raise ParserError(
                "A single record in your paste is too large to parse "
                "(hit max_tokens even after auto-chunking). Try trimming "
                "very long charge descriptions or supplemental narratives."
            )
        all_parsed.append(p)

    # Merge
    merged = {
        "subject": {},
        "records": [],
        "client": None,
        "notes": [f"Paste was auto-chunked into {len(chunks)} calls due to size."],
        "confidence": "high",
        "confidence_reason": "",
    }
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    for p in all_parsed:
        if p.get("subject") and not merged["subject"]:
            merged["subject"] = p["subject"]
        if p.get("client") and not merged["client"]:
            merged["client"] = p["client"]
        merged["records"].extend(p.get("records") or [])
        for n in p.get("notes") or []:
            if n not in merged["notes"]:
                merged["notes"].append(n)
        c = p.get("confidence") or "medium"
        if confidence_rank.get(c, 2) < confidence_rank.get(merged["confidence"], 2):
            merged["confidence"] = c
            merged["confidence_reason"] = p.get("confidence_reason", "")

    if not merged["confidence_reason"]:
        merged["confidence_reason"] = (
            f"Confidence rolled up as worst-case across {len(chunks)} "
            "auto-chunked calls."
        )

    return merged


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
