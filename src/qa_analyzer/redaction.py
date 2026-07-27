"""PII redaction pipeline.

Strips identifiers before sending to Claude and reinjects the real
values into parser output locally. Even if Anthropic's default 30-day
retention kept our request, the retained content would be structurally
intact but personally anonymized.

Strategy: consistent pseudonymization. Each real PII value gets a fake
value of the same shape (names look like names, dates like dates).
The mapping is one-to-one, so the same person mentioned three times
in the paste becomes the same pseudonym in all three places.

What we redact:
    - Names (labeled, then global-replace once known)
    - Dates of birth (contextual: only when a DOB label is nearby)
    - SSNs (any format, full or partial)
    - Full street addresses (street # + name)
    - Phone numbers
    - Email addresses
    - Case numbers (labeled)

What we keep (needed by the parser):
    - Charge descriptions
    - Offense-level markers ("F3", "M", "felony")
    - Disposition language
    - Court names, cities, counties, states
    - Non-DOB dates (arrest, filing, disposition, release)
    - Jurisdiction / source labels
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Fake identity pools (deterministic, cycle-safe)
# ---------------------------------------------------------------------------
# Fake names look real enough that Claude's extraction behaves normally.
# We pull them in order (A, B, C, ...) so the redacted text is stable
# and human-reviewable.

_FAKE_FIRSTS = [
    "Aria", "Blake", "Casey", "Dana", "Ellis", "Finley", "Gray", "Harper",
    "Indigo", "Jules", "Kai", "Logan", "Morgan", "Noel", "Oakley", "Parker",
    "Quinn", "Reese", "Sage", "Tatum", "Umber", "Vale", "Wren", "Xen",
    "Yael", "Zephyr",
]
_FAKE_LASTS = [
    "Ashford", "Blythe", "Caldwell", "Deveraux", "Elmswood", "Fairbrook",
    "Gladstone", "Hollowell", "Ironwood", "Junipero", "Kingsley", "Larkspur",
    "Merriweather", "Nightingale", "Oakenshaw", "Pemberton", "Quillfield",
    "Ravensdale", "Silverbrook", "Thornbury", "Underbridge", "Valebrook",
    "Wentworth", "Xanthome", "Yellowbrook", "Zephyrine",
]
_FAKE_MIDDLES = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J",
                 "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T",
                 "U", "V", "W", "X", "Y", "Z"]


# ---------------------------------------------------------------------------
# PII Map
# ---------------------------------------------------------------------------


@dataclass
class Entity:
    """One redacted PII value + its pseudonym."""

    kind: str            # "name", "dob", "ssn", "address", "phone", "email", "case"
    real: str            # original value
    fake: str            # pseudonym used in redacted text
    # Names have parts: {"first": "John", "middle": "R", "last": "Smith"}
    parts: dict = field(default_factory=dict)


@dataclass
class PIIMap:
    """Container of real→fake substitutions.

    Kept in-memory only; never persisted. The Streamlit session owns
    the map for the duration of a single parse+substitute round trip.
    """

    entities: list[Entity] = field(default_factory=list)

    # ---- lookups ----------------------------------------------------------

    def find_by_fake(self, kind: str, fake_value: str) -> Optional[Entity]:
        for e in self.entities:
            if e.kind == kind and e.fake == fake_value:
                return e
        return None

    def find_by_real(self, kind: str, real_value: str) -> Optional[Entity]:
        for e in self.entities:
            if e.kind == kind and e.real.lower() == real_value.lower():
                return e
        return None

    # ---- summary ----------------------------------------------------------

    def stats(self) -> dict:
        """Return {"name": 2, "dob": 1, ...} counts by kind."""
        out: dict[str, int] = {}
        for e in self.entities:
            out[e.kind] = out.get(e.kind, 0) + 1
        return out

    # ---- substitution -----------------------------------------------------

    def substitute_string(self, s: str) -> str:
        """Replace all fake values in a string with the real values.

        Uses word boundaries to prevent short numeric fakes (e.g. an
        SSN last-4 like "0001") from matching as substrings inside
        longer tokens like case numbers ("CASE-000001") or record ids.
        Longer fakes are replaced first as a further safety net.

        Special-case: SSN last-4 substitution uses parts.fake_last4 →
        parts.real_last4 so we can restore last-4 values that Claude
        extracted from a fake full SSN.
        """
        if not s:
            return s
        for e in sorted(self.entities, key=lambda x: -len(x.fake)):
            if e.fake:
                s = re.sub(
                    rf"\b{re.escape(e.fake)}\b",
                    lambda _m, r=e.real: r,
                    s,
                )
            if e.kind == "ssn" and e.parts.get("fake_last4"):
                fl = e.parts["fake_last4"]
                if fl and fl != e.fake:
                    s = re.sub(
                        rf"\b{re.escape(fl)}\b",
                        lambda _m, r=e.parts["real_last4"]: r,
                        s,
                    )
        return s

    def substitute_parsed(self, parsed: dict) -> dict:
        """Walk parser JSON and substitute all fake values back.

        We walk the whole dict recursively so we don't have to know
        every field name; anything that string-matches a fake gets the
        real value put back. Then we do a special pass for name parts
        because the parser splits names into first/middle/last but our
        map stores full-name substitution.
        """
        # Recursive string substitution
        parsed = _walk_replace(parsed, self.substitute_string)

        # Fix up name parts: if the parser extracted a fake first/last
        # into first_name/last_name/middle_name, replace with real parts.
        _replace_name_parts(parsed.get("subject"), self)
        for rec in parsed.get("records", []):
            _replace_record_name_parts(rec, self)

        return parsed


def _walk_replace(obj, fn):
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, list):
        return [_walk_replace(v, fn) for v in obj]
    if isinstance(obj, dict):
        return {k: _walk_replace(v, fn) for k, v in obj.items()}
    return obj


def _replace_name_parts(subject: Optional[dict], pmap: PIIMap):
    """If the parser extracted fake name parts into a subject, swap
    them for the real parts we captured during redaction."""
    if not subject:
        return
    for e in pmap.entities:
        if e.kind != "name" or not e.parts:
            continue
        # First name
        if subject.get("first_name") == e.parts.get("_fake_first"):
            subject["first_name"] = e.parts.get("first", "")
        # Middle name
        if subject.get("middle_name") == e.parts.get("_fake_middle"):
            subject["middle_name"] = e.parts.get("middle") or None
        # Last name
        if subject.get("last_name") == e.parts.get("_fake_last"):
            subject["last_name"] = e.parts.get("last", "")

    # SSN last-4 field on the subject
    for e in pmap.entities:
        if e.kind != "ssn" or not e.parts:
            continue
        fl = e.parts.get("fake_last4")
        rl = e.parts.get("real_last4")
        if fl and rl and subject.get("ssn_last4") == fl:
            subject["ssn_last4"] = rl


def _replace_record_name_parts(record: dict, pmap: PIIMap):
    for e in pmap.entities:
        if e.kind != "name" or not e.parts:
            continue
        if record.get("record_first_name") == e.parts.get("_fake_first"):
            record["record_first_name"] = e.parts.get("first", "")
        if record.get("record_middle_name") == e.parts.get("_fake_middle"):
            record["record_middle_name"] = e.parts.get("middle") or None
        if record.get("record_last_name") == e.parts.get("_fake_last"):
            record["record_last_name"] = e.parts.get("last", "")

    # SSN last-4 field on the record
    for e in pmap.entities:
        if e.kind != "ssn" or not e.parts:
            continue
        fl = e.parts.get("fake_last4")
        rl = e.parts.get("real_last4")
        if fl and rl and record.get("record_ssn_last4") == fl:
            record["record_ssn_last4"] = rl


# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

# Names (label-based — highest precision path)
_NAME_LABEL = (
    r"(?:Name|Candidate|Subject|Defendant|Co\-defendant|Applicant|Individual|"
    r"Full Name|Party|Respondent|Petitioner|Complainant|Party Name|"
    r"Record Name|Person)"
)
# Matches: "Name: John Robert Smith", "Defendant Name: J. R. Smith".
# IMPORTANT: internal whitespace is [ \t] only — must not cross newlines,
# otherwise the regex will greedily grab the label from the next line as
# part of the person's name (e.g. "Name: John Smith\nDOB" → last=DOB).
_NAME_LABELED_RE = re.compile(
    rf"({_NAME_LABEL})([ \t]+Name)?[ \t]*[:\-][ \t]*"
    r"([A-Z][A-Za-z\'\-\.]+(?:[ \t]+[A-Z][A-Za-z\'\-\.]*){1,4})",
    re.IGNORECASE,
)
# "SMITH, JOHN A" pattern (courthouse standard)
_NAME_INVERTED_RE = re.compile(
    r"\b([A-Z][A-Z\'\-]{1,})\s*,\s*([A-Z][A-Za-z\'\-]+)"
    r"(?:\s+([A-Z])\.?)?\b"
)

# SSN — either a strict dashed pattern OR a labeled numeric.
# We DON'T match bare 9-digit runs (too many false positives, e.g. case
# numbers, docket IDs). Require either dashes or an SSN label.
_SSN_DASHED_RE = re.compile(r"\b(\d{3}-\d{2}-\d{4})\b")
_SSN_LABELED_RE = re.compile(
    r"(?P<label>SSN|SS#|Social(?:\s+Security)?(?:\s+(?:#|No|Number|Num))?)"
    r"[ \t]*[:\-#]?[ \t]*(?P<value>\d{3}[- ]?\d{2}[- ]?\d{4}|\d{9})",
    re.IGNORECASE,
)
_SSN_LAST4_RE = re.compile(
    r"\b(?:X{3}[- ]?X{2}[- ]?(\d{4})|[X\*]{5,7}[- ]?(\d{4}))\b"
)

# DOB — only redact dates immediately following a DOB label
_DOB_RE = re.compile(
    r"(?P<label>(?:DOB|Date of Birth|Birth Date|D\.O\.B\.|Born(?:\s+on)?))"
    r"\s*[:\-]?\s*"
    r"(?P<value>"
    r"(?:\d{1,2}[-/]\d{1,2}[-/]\d{2,4})"     # MM/DD/YYYY
    r"|(?:\d{4}[-/]\d{1,2}[-/]\d{1,2})"      # YYYY-MM-DD
    r"|(?:[A-Z][a-z]+\s+\d{1,2},?\s+\d{4})"  # January 5, 1985
    r")",
    re.IGNORECASE,
)

# Phone — no leading \b (fails before "(") and no trailing \b (fails
# after ")"). Use lookbehind / lookahead for non-digit boundaries.
_PHONE_RE = re.compile(
    r"(?<!\d)"
    r"(?:\+?1[- .]?)?"
    r"(?:\(\d{3}\)[ .-]?|\d{3}[- .])"
    r"\d{3}[- .]\d{4}"
    r"(?!\d)"
)

# Email
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"
)

# Street address (street number + name + type). Stop at newline, comma
# (city/state usually follows), or another common separator.
_ADDR_RE = re.compile(
    r"\b(\d{1,6}\s+(?:[NSEW]\.?\s+)?[A-Z][A-Za-z0-9\.\-\s]{2,60}?"
    r"(?:Street|St\.?|Road|Rd\.?|Avenue|Ave\.?|Boulevard|Blvd\.?|"
    r"Lane|Ln\.?|Drive|Dr\.?|Court|Ct\.?|Way|Place|Pl\.?|"
    r"Circle|Cir\.?|Terrace|Ter\.?|Highway|Hwy\.?|"
    r"Parkway|Pkwy\.?|Trail|Tr\.?))\b"
)

# Case numbers (labeled)
_CASE_RE = re.compile(
    r"(?P<label>(?:Case(?:\s+Number|\s+No\.?|\s+#)?|Docket(?:\s+No\.?|\s+#)?"
    r"|File\s+No\.?|Citation(?:\s+No\.?|\s+#)?))\s*[:\-]?\s*"
    r"(?P<value>[A-Z0-9][A-Z0-9\-\.\/]{2,30})",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def _next_fake_name(pmap: PIIMap) -> tuple[str, str]:
    """Return (first, last) fake pair not already used."""
    used = {(e.parts.get("_fake_first"), e.parts.get("_fake_last"))
            for e in pmap.entities if e.kind == "name"}
    for f in _FAKE_FIRSTS:
        for l in _FAKE_LASTS:
            if (f, l) not in used:
                return f, l
    # Extremely unlikely fallback
    return f"Person{len(pmap.entities)}", f"Placeholder{len(pmap.entities)}"


def _next_fake_middle(pmap: PIIMap) -> str:
    used = {e.parts.get("middle") for e in pmap.entities
            if e.kind == "name" and e.parts.get("middle")}
    for m in _FAKE_MIDDLES:
        if m not in used:
            return m
    return "Z"


def _next_index(pmap: PIIMap, kind: str) -> int:
    return sum(1 for e in pmap.entities if e.kind == kind)


def _register_name(real_name: str, pmap: PIIMap) -> Entity:
    """Register a name (may include middle) and return the Entity.

    Idempotent: if we've already seen this name, return the existing
    entity so all occurrences share the same fake identity.
    """
    real_name = real_name.strip()

    # If we already have this exact name, reuse.
    existing = pmap.find_by_real("name", real_name)
    if existing:
        return existing

    # Split into first / middle / last
    parts = real_name.split()
    # Handle "Smith, John R" (inverted, single comma)
    if "," in real_name and len(parts) >= 2:
        # e.g. "Smith," "John" "R" → last=Smith, first=John, middle=R
        comma_idx = next(i for i, p in enumerate(parts) if p.endswith(","))
        last = parts[comma_idx].rstrip(",")
        first = parts[comma_idx + 1] if comma_idx + 1 < len(parts) else ""
        middle = parts[comma_idx + 2] if comma_idx + 2 < len(parts) else ""
    else:
        first = parts[0] if parts else ""
        last = parts[-1] if len(parts) >= 2 else ""
        middle = " ".join(parts[1:-1]) if len(parts) >= 3 else ""

    # Also check if the same person appears with a different variant
    # (e.g. "John R Smith" vs "John Robert Smith"). We match on
    # (first_initial, last).
    for e in pmap.entities:
        if e.kind != "name":
            continue
        if (e.parts.get("first", "")[:1].lower() == first[:1].lower()
                and e.parts.get("last", "").lower() == last.lower()
                and last):
            # Fuzzy match — same person, different formatting.
            return e

    # Register a new name.
    fake_first, fake_last = _next_fake_name(pmap)
    fake_middle = _next_fake_middle(pmap) if middle else ""
    fake_full = f"{fake_first} {fake_middle + ' ' if fake_middle else ''}{fake_last}"

    entity = Entity(
        kind="name",
        real=real_name,
        fake=fake_full.strip(),
        parts={
            "first": first,
            "middle": middle or None,
            "last": last,
            "_fake_first": fake_first,
            "_fake_middle": fake_middle if fake_middle else None,
            "_fake_last": fake_last,
        },
    )
    pmap.entities.append(entity)
    return entity


def _register_dob(real_dob: str, pmap: PIIMap) -> Entity:
    """DOB fake: 1900-01-01, 1900-01-02, ...

    We normalize the real value to ISO (YYYY-MM-DD) when possible so
    that substitution back into the parser output produces a parseable
    date regardless of the input format ("October 5, 1976" →
    "1976-10-05"). Non-parseable formats are stored as-is.
    """
    normalized = _normalize_dob(real_dob)
    existing = pmap.find_by_real("dob", normalized)
    if existing:
        return existing
    idx = _next_index(pmap, "dob") + 1
    fake = f"1900-01-{idx:02d}"
    entity = Entity(
        kind="dob",
        real=normalized,
        fake=fake,
        # Preserve the original raw string too — useful for the round-trip
        # debug/validator check (see round_trip_check).
        parts={"raw": real_dob},
    )
    pmap.entities.append(entity)
    return entity


_DOB_FORMATS = [
    "%Y-%m-%d", "%Y/%m/%d",
    "%m/%d/%Y", "%m-%d-%Y",
    "%m/%d/%y", "%m-%d-%y",
    "%B %d, %Y", "%B %d %Y",
    "%b %d, %Y", "%b %d %Y",
]


def _normalize_dob(v: str) -> str:
    """Return ISO YYYY-MM-DD for common inputs; else the original string."""
    if not v:
        return v
    v = v.strip().rstrip(".,")
    for fmt in _DOB_FORMATS:
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            continue
    return v


def _register_ssn(real_ssn: str, pmap: PIIMap) -> Entity:
    """Register an SSN. Fake last-4 = 0001, 0002, ...

    We also compute and store a `real_last4` so that when Claude
    extracts just the last 4 digits from a fake full SSN, our
    substitution can still put back the correct real last-4 value.
    """
    existing = pmap.find_by_real("ssn", real_ssn)
    if existing:
        return existing
    idx = _next_index(pmap, "ssn") + 1
    normalized = real_ssn.replace("-", "").replace(" ", "")

    # If input is full SSN, use a full fake; if last-4 only, use last-4.
    if len(normalized) == 9:
        fake = f"999-99-{idx:04d}"
        real_last4 = normalized[-4:]
    else:
        fake = f"{idx:04d}"
        real_last4 = normalized  # already 4 chars

    fake_last4 = f"{idx:04d}"
    entity = Entity(
        kind="ssn",
        real=real_ssn,
        fake=fake,
        parts={"real_last4": real_last4, "fake_last4": fake_last4},
    )
    pmap.entities.append(entity)
    return entity


def _register_phone(real_phone: str, pmap: PIIMap) -> Entity:
    existing = pmap.find_by_real("phone", real_phone)
    if existing:
        return existing
    idx = _next_index(pmap, "phone") + 1
    fake = f"555-000-{idx:04d}"
    entity = Entity(kind="phone", real=real_phone, fake=fake)
    pmap.entities.append(entity)
    return entity


def _register_email(real_email: str, pmap: PIIMap) -> Entity:
    existing = pmap.find_by_real("email", real_email)
    if existing:
        return existing
    idx = _next_index(pmap, "email") + 1
    fake = f"user{idx}@example.invalid"
    entity = Entity(kind="email", real=real_email, fake=fake)
    pmap.entities.append(entity)
    return entity


def _register_address(real_addr: str, pmap: PIIMap) -> Entity:
    existing = pmap.find_by_real("address", real_addr)
    if existing:
        return existing
    idx = _next_index(pmap, "address") + 1
    fake = f"{100 + idx} Placeholder Street"
    entity = Entity(kind="address", real=real_addr, fake=fake)
    pmap.entities.append(entity)
    return entity


def _register_case(real_case: str, pmap: PIIMap) -> Entity:
    existing = pmap.find_by_real("case", real_case)
    if existing:
        return existing
    idx = _next_index(pmap, "case") + 1
    fake = f"CASE-{idx:06d}"
    entity = Entity(kind="case", real=real_case, fake=fake)
    pmap.entities.append(entity)
    return entity


# ---------------------------------------------------------------------------
# Main redact() function
# ---------------------------------------------------------------------------


# Internal markers used during redaction to avoid cascade bugs (where a
# fake value from an early pattern accidentally matches a later
# pattern's regex). Uses non-printable bytes that won't appear in real
# operator input.
_MARK_OPEN = "\x00\x01P"
_MARK_CLOSE = "\x00"


def _make_marker(index: int) -> str:
    return f"{_MARK_OPEN}{index}{_MARK_CLOSE}"


def redact(text: str) -> tuple[str, PIIMap]:
    """Redact PII from a paste; return (redacted_text, PIIMap).

    The returned text can be safely sent to Claude — none of the real
    identifiers are present, but structural fields (charges,
    dispositions, dates, jurisdictions, court names) remain intact.

    Two-phase to avoid cascade bugs:
      1. Scan patterns, register entities in pmap, replace with unique
         internal markers (non-printable bytes).
      2. Substitute markers with fake values at the very end.

    Because markers don't look like any real PII, subsequent patterns
    can't re-match already-redacted regions.
    """
    pmap = PIIMap()
    if not text:
        return text, pmap

    # Marker index shared across all patterns
    marker_entities: list[Entity] = []

    def _place(entity: Entity) -> str:
        marker_entities.append(entity)
        return _make_marker(len(marker_entities) - 1)

    # ---- Names (labeled path — highest precision) ------------------------
    def _sub_labeled_name(m: re.Match) -> str:
        label = m.group(1)
        suffix = m.group(2) or ""
        name = m.group(3).strip()
        # Skip obvious false positives (short single tokens, non-names).
        if len(name) < 4 or name.lower() in _LABEL_STOPWORDS:
            return m.group(0)
        e = _register_name(name, pmap)
        return f"{label}{suffix}: {_place(e)}"

    text = _NAME_LABELED_RE.sub(_sub_labeled_name, text)

    # ---- Names (inverted LAST, First format) -----------------------------
    def _sub_inverted_name(m: re.Match) -> str:
        last_raw = m.group(1)
        first = m.group(2)
        mi = m.group(3) or ""
        # Only accept true LAST-comma-First patterns (last is all-caps)
        if not last_raw.isupper() or len(last_raw) < 3:
            return m.group(0)
        real = f"{last_raw.title()}, {first}"
        if mi:
            real = f"{real} {mi}"
        e = _register_name(real, pmap)
        # For inverted format we can't use a single marker for the whole
        # comma-separated pair without changing structure. Use one marker
        # per part so we can substitute back precisely.
        # Attach a special "inverted" entity with combined fake string.
        return _place(e)

    text = _NAME_INVERTED_RE.sub(_sub_inverted_name, text)

    # ---- SSN --------------------------------------------------------------
    # Order matters: labeled first (most specific), then dashed, then last-4.
    def _sub_ssn_labeled(m):
        label = m.group("label")
        value = m.group("value")
        e = _register_ssn(value, pmap)
        return f"{label}: {_place(e)}"
    text = _SSN_LABELED_RE.sub(_sub_ssn_labeled, text)

    def _sub_ssn_dashed(m):
        e = _register_ssn(m.group(1), pmap)
        return _place(e)
    text = _SSN_DASHED_RE.sub(_sub_ssn_dashed, text)

    def _sub_ssn_last4(m):
        real = m.group(1) or m.group(2)
        e = _register_ssn(real, pmap)
        # Preserve mask style around the fake last-4
        return f"XXX-XX-{_place(e)}"
    text = _SSN_LAST4_RE.sub(_sub_ssn_last4, text)

    # ---- DOB --------------------------------------------------------------
    def _sub_dob(m):
        label = m.group("label")
        value = m.group("value")
        e = _register_dob(value, pmap)
        return f"{label}: {_place(e)}"
    text = _DOB_RE.sub(_sub_dob, text)

    # ---- Email ------------------------------------------------------------
    def _sub_email(m):
        e = _register_email(m.group(0), pmap)
        return _place(e)
    text = _EMAIL_RE.sub(_sub_email, text)

    # ---- Phone ------------------------------------------------------------
    def _sub_phone(m):
        e = _register_phone(m.group(0), pmap)
        return _place(e)
    text = _PHONE_RE.sub(_sub_phone, text)

    # ---- Address ---------------------------------------------------------
    def _sub_addr(m):
        e = _register_address(m.group(1), pmap)
        return _place(e)
    text = _ADDR_RE.sub(_sub_addr, text)

    # ---- Case numbers -----------------------------------------------------
    def _sub_case(m):
        label = m.group("label")
        value = m.group("value")
        e = _register_case(value, pmap)
        return f"{label}: {_place(e)}"
    text = _CASE_RE.sub(_sub_case, text)

    # ---- Global replacement of registered name parts ---------------------
    # Runs LAST, so email/phone/etc that contain first names (e.g.
    # "john@example.com") get caught by their own more-specific patterns
    # first. This step catches unlabeled narrative mentions like
    # "John Smith was arrested…".
    for e in list(pmap.entities):
        if e.kind != "name":
            continue
        text = _redact_name_globals(text, e, _place)

    # ---- Phase 2: resolve markers -> fake values -------------------------
    text = _resolve_markers(text, marker_entities)

    return text, pmap


def _redact_name_globals(text: str, entity: Entity, place_fn) -> str:
    """Replace remaining un-labeled occurrences of a known name.

    Each match gets its own marker pointing to the same entity so the
    round-trip substitution restores the real value.
    """
    real_first = entity.parts.get("first", "")
    real_last = entity.parts.get("last", "")
    real_middle = entity.parts.get("middle") or ""

    def _mark_full(_):
        return place_fn(entity)

    if real_first and real_last:
        # Longer patterns first to avoid partial-match issues.
        if real_middle:
            pat = rf"\b{re.escape(real_first)}[ \t]+{re.escape(real_middle)}[ \t]+{re.escape(real_last)}\b"
            text = re.sub(pat, _mark_full, text, flags=re.IGNORECASE)
        # First + middle-initial + Last
        text = re.sub(
            rf"\b{re.escape(real_first)}[ \t]+[A-Z]\.?[ \t]+{re.escape(real_last)}\b",
            _mark_full, text, flags=re.IGNORECASE,
        )
        # First + Last
        text = re.sub(
            rf"\b{re.escape(real_first)}[ \t]+{re.escape(real_last)}\b",
            _mark_full, text, flags=re.IGNORECASE,
        )
        # Last, First format
        text = re.sub(
            rf"\b{re.escape(real_last)},[ \t]+{re.escape(real_first)}\b",
            _mark_full, text, flags=re.IGNORECASE,
        )
        # Standalone last name (distinctive, 4+ chars)
        if len(real_last) >= 4:
            text = re.sub(
                rf"\b{re.escape(real_last)}\b",
                _mark_full, text, flags=re.IGNORECASE,
            )
        # Standalone first name (distinctive, 4+ chars, avoids common words)
        if len(real_first) >= 4:
            text = re.sub(
                rf"\b{re.escape(real_first)}\b",
                _mark_full, text, flags=re.IGNORECASE,
            )
    return text


_MARKER_RE = re.compile(re.escape(_MARK_OPEN) + r"(\d+)" + re.escape(_MARK_CLOSE))


def _resolve_markers(text: str, entities: list) -> str:
    """Replace all internal markers with their entity fake values."""
    def _sub(m):
        idx = int(m.group(1))
        if idx < 0 or idx >= len(entities):
            return m.group(0)
        return entities[idx].fake
    return _MARKER_RE.sub(_sub, text)


# Stopwords for the labeled-name path — sometimes fields have generic
# labels like "Name: N/A" that shouldn't get pseudonymized.
_LABEL_STOPWORDS = {
    "n/a", "na", "unknown", "not available", "unspecified",
    "none", "null", "tbd", "pending",
}


def _replace_word(text: str, pattern: str, replacement: str) -> str:
    """Case-insensitive whole-word replacement using regex boundaries."""
    return re.sub(rf"\b{pattern}\b", replacement, text, flags=re.IGNORECASE)


# ---------------------------------------------------------------------------
# Round-trip test helper
# ---------------------------------------------------------------------------


def round_trip_check(text: str) -> dict:
    """Redact + substitute back a string; return diagnostics.

    Useful for the validator: shows what got redacted and confirms that
    substitution restores the original values.
    """
    redacted, pmap = redact(text)
    restored = pmap.substitute_string(redacted)
    return {
        "original_len": len(text),
        "redacted_len": len(redacted),
        "restored_matches_original": restored == text,
        "stats": pmap.stats(),
        "entities": [
            {"kind": e.kind, "real_preview": _preview(e.real),
             "fake": e.fake}
            for e in pmap.entities
        ],
    }


def _preview(s: str, n: int = 12) -> str:
    """Trim + mask a preview of a real PII value for logs / diagnostics."""
    s = s.strip()
    if len(s) <= n:
        return s[:2] + "***"
    return s[:2] + "***" + s[-2:]
