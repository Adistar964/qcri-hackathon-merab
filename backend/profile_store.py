"""
Saved Information ("My Info") store — a small, optional profile the agent reuses to
auto-fill forms.

Behaviour the user asked for:
  • A profile section where you can fill name, email, phone, DOB, QID, QID expiry,
    passport, etc. — all OPTIONAL.
  • When filling a form, the agent first looks here; only if a field is missing does
    it ask the user (and it can offer to save the answer for next time).

Storage: a single JSON file under the agent workspace (local, single-user app).
We deliberately do NOT store passwords here — login credentials stay in memory only
(see agent.py). This file holds non-secret identity fields the user chose to save.
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import re
import threading
from typing import Any

WORKSPACE = pathlib.Path(os.getenv("AGENT_WORKSPACE", "agent_workspace")).resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)
PROFILE_PATH = WORKSPACE / "profile.json"

_LOCK = threading.Lock()

# Canonical, ordered field set shown in the "Saved Information" panel. All optional.
#   key       : stable storage key (also used by workflows.INPUT_SPECS -> profile)
#   label     : human label in the UI
#   type      : input hint for the UI (text | email | tel | date)
#   format    : expected text format for date fields the agent types into forms
PROFILE_FIELDS: list[dict[str, Any]] = [
    {"key": "full_name", "label": "Full Name", "type": "text"},
    {"key": "email", "label": "Email", "type": "email"},
    {"key": "phone", "label": "Phone", "type": "tel"},
    {"key": "dob", "label": "Date of Birth", "type": "date", "format": "yyyy/mm/dd"},
    {"key": "qid", "label": "Qatar ID (QID)", "type": "text"},
    {"key": "qid_expiry", "label": "QID Expiry", "type": "date", "format": "yyyy/mm/dd"},
    {"key": "passport", "label": "Passport Number", "type": "text"},
    {"key": "passport_expiry", "label": "Passport Expiry", "type": "date", "format": "yyyy/mm/dd"},
    {"key": "residence_expiry", "label": "Residence Permit Expiry", "type": "date", "format": "yyyy/mm/dd"},
    {"key": "nationality", "label": "Nationality", "type": "text"},
]

_ALLOWED_KEYS = {f["key"] for f in PROFILE_FIELDS} | {"patientID"}  # patientID captured after PHCC login


def field_meta() -> list[dict[str, Any]]:
    """The field definitions for the UI (labels, types, formats)."""
    return [dict(f) for f in PROFILE_FIELDS]


def load_profile() -> dict[str, str]:
    """Return the saved profile (empty dict if none / unreadable)."""
    with _LOCK:
        if not PROFILE_PATH.is_file():
            return {}
        try:
            data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    if not isinstance(data, dict):
        return {}
    # Keep only known keys, coerce to trimmed strings.
    return {k: str(v).strip() for k, v in data.items() if k in _ALLOWED_KEYS and str(v).strip()}


def save_profile(values: dict[str, Any]) -> dict[str, str]:
    """Replace the profile with `values` (only known, non-empty keys are kept)."""
    clean = {k: str(v).strip() for k, v in (values or {}).items()
             if k in _ALLOWED_KEYS and str(v).strip()}
    with _LOCK:
        PROFILE_PATH.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean


def update_profile(partial: dict[str, Any]) -> dict[str, str]:
    """Merge `partial` into the saved profile (used when the user lets us remember a field)."""
    current = load_profile()
    current.update({k: str(v).strip() for k, v in (partial or {}).items()
                    if k in _ALLOWED_KEYS and str(v).strip()})
    return save_profile(current)


# --------------------------------------------------------------------------- #
# QID image -> profile fields, via Fanar vision (Fanar-Oryx-IVU). The user uploads
# a photo of their Qatar ID and the model reads the fields to fill the profile.
# --------------------------------------------------------------------------- #
_DATE_KEYS = {f["key"] for f in PROFILE_FIELDS if f.get("type") == "date"}

# Map the various keys the model might return onto our canonical profile keys.
_KEY_ALIASES = {
    "name": "full_name", "fullname": "full_name", "holder": "full_name", "holder_name": "full_name",
    "id": "qid", "id_number": "qid", "idnumber": "qid", "qid_number": "qid", "qid_no": "qid",
    "card_number": "qid", "civil_id": "qid", "national_id": "qid", "serial": "qid", "serial_number": "qid",
    "date_of_birth": "dob", "birth_date": "dob", "dateofbirth": "dob", "birthdate": "dob",
    "expiry": "qid_expiry", "expiry_date": "qid_expiry", "id_expiry": "qid_expiry",
    "qid_expiry_date": "qid_expiry", "qid_expiry": "qid_expiry", "expiry_date_of_qid": "qid_expiry",
    "expiration": "qid_expiry", "expiration_date": "qid_expiry", "card_expiry": "qid_expiry",
    "passport_number": "passport", "passport_no": "passport", "passportnumber": "passport",
    "country": "nationality", "nationality_country": "nationality",
}

# The user's known-working prompt for Fanar-Oryx-IVU-2 (a strict/complex prompt made the
# vision model under-extract or reply in prose). Keep it simple; nudge JSON-only output.
# Kept as the LAST-RESORT single-step fallback (see extract_from_image).
_QID_VISION_PROMPT = (
    "Extract these things if available: Full Name, QID Number, Nationality, QID expiry Date, "
    "Passport Number fields in the form of json. Respond with only the JSON object."
)

# Two-step extraction (more reliable than asking the vision model for JSON directly):
#   1) the VISION model transcribes ALL text on the card (OCR — it is far better at reading than at
#      emitting clean JSON, which is why the single-step path under-extracted);
#   2) the TEXT model turns that transcript into structured JSON fields.
_QID_OCR_PROMPT = (
    "This is a photo of a Qatar ID (QID) card. Read and transcribe ALL the text you can see on the "
    "card exactly as written — include both the English and the Arabic text and every number. List "
    "each printed label together with its value (e.g. 'ID Number: ...', 'Name: ...', 'Nationality: "
    "...', 'Date of Birth: ...', 'Expiry: ...'). Do not summarise."
)
_QID_FIELD_SYSTEM = (
    "You extract structured fields from the raw text of a Qatar ID (QID) card. Return ONLY a JSON "
    "object (no prose, no markdown fences, no comments) using these keys when the value is present: "
    "full_name, qid, nationality, dob, qid_expiry, passport. The QID number ('qid') is the long "
    "(usually 11-digit) ID / serial number. "
    "IMPORTANT: every value MUST be in ENGLISH only. A Qatar ID prints each field in both English "
    "and Arabic — always use the ENGLISH text. If a value appears only in Arabic, translate or "
    "transliterate it into English (e.g. the name into Latin letters, the nationality into its "
    "English country name). Never return Arabic characters. Format every date as yyyy-mm-dd and "
    "write numbers as digits. Omit any key you cannot find. If nothing is found, return {}."
)


def _coerce_text(raw: Any) -> str:
    """Vision/chat content occasionally arrives as a list of parts — coerce to a plain string."""
    if isinstance(raw, list):
        raw = " ".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in raw)
    return str(raw or "").strip()


# Arabic letter ranges — used to enforce English-only field values (the user wants the extracted
# JSON in English; the QID prints every field in both English and Arabic).
_ARABIC_RE = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")


def _has_arabic(text: str) -> bool:
    return bool(_ARABIC_RE.search(text or ""))


def _canonical_key(raw: str) -> str:
    """Map whatever key the vision model returns onto our canonical profile key.
    First try the exact alias table, then fall back to tolerant substring heuristics so we
    catch free-form keys the model invents (e.g. 'QID expiry Date', 'Holder Full Name')."""
    norm = re.sub(r"[^a-z0-9]+", "_", str(raw).strip().lower()).strip("_")
    if norm in _KEY_ALIASES:
        return _KEY_ALIASES[norm]
    if norm in _ALLOWED_KEYS:
        return norm
    # Substring heuristics (order matters — more specific first).
    if "passport" in norm:
        return "passport_expiry" if "expir" in norm or "expiry" in norm else "passport"
    if "expir" in norm:                       # any expiry on a QID card == the QID expiry
        return "qid_expiry"
    if "birth" in norm or norm == "dob":
        return "dob"
    if "nation" in norm or "country" in norm:
        return "nationality"
    if "name" in norm:
        return "full_name"
    if "qid" in norm or "civil" in norm or norm.endswith("id") or "id_number" in norm or norm == "id":
        return "qid"
    return norm


def _parse_json_object(text: str) -> dict[str, Any]:
    if not text:
        return {}
    start = text.find("{")
    if start == -1:
        return {}
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1])
                    return obj if isinstance(obj, dict) else {}
                except json.JSONDecodeError:
                    return {}
    return {}


def _iso_date(text: str) -> str:
    """Normalise a date string to yyyy-mm-dd (what the UI date inputs + storage use)."""
    nums = re.findall(r"\d+", text or "")
    if len(nums) < 3:
        return text
    a, b, c = nums[0], nums[1], nums[2]
    if len(a) == 4:
        y, m, d = a, b, c
    elif len(c) == 4:
        d, m, y = a, b, c
    else:
        return text
    try:
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    except ValueError:
        return text


def extract_from_image(client: Any, image_bytes: bytes, mime_type: str = "image/jpeg") -> dict[str, Any]:
    """Read a QID photo and return the profile fields detected. Two stages:
        1) the VISION model OCRs the card to raw text;
        2) the TEXT model extracts structured JSON fields from that text.
    Falls back to JSON-in-the-OCR, then to a single-step vision→JSON call, so it's never worse than
    the old path. Does NOT save — the caller decides whether to persist (the UI lets the user review)."""
    b64 = base64.b64encode(image_bytes).decode()

    # Stage 1 — vision model transcribes the card to plain text.
    try:
        ocr = _coerce_text(client.see_image(b64, _QID_OCR_PROMPT, max_tokens=700, mime_type=mime_type))
    except Exception as exc:  # noqa: BLE001
        return {"fields": {}, "error": str(exc)}

    # Stage 2 — text model turns the transcript into JSON fields.
    data: dict[str, Any] = {}
    if ocr:
        try:
            raw = client.chat(
                [{"role": "system", "content": _QID_FIELD_SYSTEM},
                 {"role": "user", "content": f"QID card text:\n{ocr}\n\nReturn the JSON object now."}],
                temperature=0.0, max_tokens=300)
            data = _parse_json_object(_coerce_text(raw))
        except Exception:  # noqa: BLE001 — fall through to the fallbacks below
            data = {}

    # Fallbacks: JSON already in the OCR text → else a last-resort single-step vision→JSON call.
    if not data and ocr:
        data = _parse_json_object(ocr)
    if not data:
        try:
            data = _parse_json_object(_coerce_text(
                client.see_image(b64, _QID_VISION_PROMPT, max_tokens=500, mime_type=mime_type)))
        except Exception:  # noqa: BLE001
            data = {}

    fields: dict[str, str] = {}
    for k, v in data.items():
        key = _canonical_key(k)
        val = str(v).strip()
        if key not in _ALLOWED_KEYS or not val or val.lower() in ("null", "none", "n/a", "-", ""):
            continue
        if key in _DATE_KEYS:
            fields[key] = _iso_date(val)
        elif _has_arabic(val):
            continue   # English-only: skip a value the model returned in Arabic
        else:
            fields[key] = val
    return {"fields": fields, "raw": (ocr[:300] if ocr else "")}
