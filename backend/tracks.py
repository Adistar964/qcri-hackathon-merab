"""
Track specialization — the agent adapts its persona, tools, and quick-actions to one
of the hackathon's three domains:

  • government  — Smart Government & Citizen Services
  • healthcare  — Healthcare Information & Patient Support
  • education   — Education & Cultural Preservation

Government reuses the real-browser + letter/document tools. Healthcare and Education
add Fanar-LLM-backed generators that produce real, saved artefacts (summaries,
instructions, lesson plans, quizzes, flashcards). Every healthcare output carries a
safety disclaimer — these tools inform, they do not diagnose.
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
from typing import Any

WORKSPACE = pathlib.Path(os.getenv("AGENT_WORKSPACE", "agent_workspace")).resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)

MEDICAL_DISCLAIMER = (
    "⚕️ This information is educational and AI-generated; it is not a medical diagnosis. "
    "Always consult a licensed clinician. In an emergency call 999 (Qatar)."
)


def _safe_path(filename: str) -> pathlib.Path:
    name = re.sub(r"[^A-Za-z0-9._؀-ۿ -]", "_", filename).strip() or "untitled.txt"
    target = (WORKSPACE / name).resolve()
    if WORKSPACE not in target.parents and target != WORKSPACE:
        raise ValueError("Refusing to write outside the agent workspace.")
    return target


def _save(filename: str, content: str) -> str:
    path = _safe_path(filename)
    path.write_text(content, encoding="utf-8")
    return str(path.relative_to(WORKSPACE.parent))


def _gen(client, system: str, user: str, max_tokens: int = 900) -> str:
    return client.chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.4,
        max_tokens=max_tokens,
    )


# =========================================================================== #
# Healthcare tools
# =========================================================================== #
def summarize_clinical_note(client, note: str, language: str = "en") -> dict[str, Any]:
    """Summarise a clinical note into a structured, clinician-friendly summary."""
    out = _gen(
        client,
        "You are a clinical documentation assistant. Summarise the note into sections: "
        "Chief Complaint, History, Findings, Assessment, Plan. Be faithful; do not invent facts.",
        f"Language: {language}.\nClinical note:\n{note}",
    )
    saved = _save("clinical_summary.txt", out + "\n\n" + MEDICAL_DISCLAIMER)
    return {"summary": out, "saved_to": saved, "disclaimer": MEDICAL_DISCLAIMER}


def drug_information(client, name: str, language: str = "en") -> dict[str, Any]:
    """Explain a medication: uses, common dosage notes, side effects, cautions."""
    out = _gen(
        client,
        "You are a careful pharmacology information assistant. Give: what it is, common uses, "
        "typical considerations, common side effects, and important cautions/interactions. "
        "Never give an individualized dose. Keep it factual and concise.",
        f"Language: {language}.\nMedication: {name}",
    )
    return {"medication": name, "information": out, "disclaimer": MEDICAL_DISCLAIMER}


def patient_instructions(client, condition: str, medications: str = "", language: str = "ar") -> dict[str, Any]:
    """Generate clear, patient-friendly care instructions and save them."""
    out = _gen(
        client,
        "You are a patient-education assistant. Write clear, kind, simple home-care instructions "
        "a patient can follow. Use short bullet points. Add when to seek urgent care.",
        f"Language: {language}.\nCondition: {condition}\nMedications: {medications or 'none specified'}",
    )
    saved = _save(f"patient_instructions_{re.sub(r'[^A-Za-z0-9]+','_',condition)[:30]}.txt", out + "\n\n" + MEDICAL_DISCLAIMER)
    return {"instructions": out, "saved_to": saved, "disclaimer": MEDICAL_DISCLAIMER}


def symptom_triage(client, symptoms: str, language: str = "en") -> dict[str, Any]:
    """Give general, safe guidance and an urgency level for described symptoms."""
    out = _gen(
        client,
        "You are a cautious triage information assistant (NOT a doctor). Given symptoms, provide: "
        "a likely urgency level (self-care / see a doctor soon / urgent care / emergency), general "
        "advice, and clear red-flag warning signs. Always err toward caution and recommend professional care.",
        f"Language: {language}.\nSymptoms: {symptoms}",
    )
    return {"guidance": out, "disclaimer": MEDICAL_DISCLAIMER}


# =========================================================================== #
# Education tools
# =========================================================================== #
def lesson_plan(client, topic: str, level: str = "secondary", language: str = "en") -> dict[str, Any]:
    """Create a structured lesson plan and save it."""
    out = _gen(
        client,
        "You are an expert curriculum designer. Produce a lesson plan with: objectives, "
        "materials, a timed activity sequence, differentiation, and assessment. Keep it practical.",
        f"Language: {language}.\nTopic: {topic}\nLevel: {level}",
        max_tokens=1100,
    )
    saved = _save(f"lesson_{re.sub(r'[^A-Za-z0-9]+','_',topic)[:30]}.txt", out)
    return {"lesson_plan": out, "saved_to": saved}


def generate_quiz(client, topic: str, num_questions: int = 5, level: str = "secondary", language: str = "en") -> dict[str, Any]:
    """Generate a quiz with an answer key and save it as JSON."""
    out = _gen(
        client,
        "You are an assessment writer. Return ONLY valid JSON: a list of objects with keys "
        "'question', 'options' (list of 4), and 'answer' (the correct option text). No prose.",
        f"Language: {language}.\nWrite {num_questions} multiple-choice questions on: {topic} (level: {level}).",
        max_tokens=1100,
    )
    # best-effort JSON extraction
    questions: Any
    try:
        start = out.find("[")
        questions = json.loads(out[start : out.rfind("]") + 1]) if start != -1 else out
    except Exception:  # noqa: BLE001
        questions = out
    saved = _save(f"quiz_{re.sub(r'[^A-Za-z0-9]+','_',topic)[:30]}.json",
                  json.dumps(questions, ensure_ascii=False, indent=2) if not isinstance(questions, str) else questions)
    return {"topic": topic, "questions": questions, "saved_to": saved}


def make_flashcards(client, topic: str, num_cards: int = 8, language: str = "ar") -> dict[str, Any]:
    """Generate front/back flashcards and save them."""
    out = _gen(
        client,
        "You are a study-tools generator. Return ONLY valid JSON: a list of objects with keys "
        "'front' and 'back'. No prose.",
        f"Language: {language}.\nMake {num_cards} flashcards for: {topic}.",
        max_tokens=900,
    )
    try:
        start = out.find("[")
        cards = json.loads(out[start : out.rfind("]") + 1]) if start != -1 else out
    except Exception:  # noqa: BLE001
        cards = out
    saved = _save(f"flashcards_{re.sub(r'[^A-Za-z0-9]+','_',topic)[:30]}.json",
                  json.dumps(cards, ensure_ascii=False, indent=2) if not isinstance(cards, str) else cards)
    return {"topic": topic, "cards": cards, "saved_to": saved}


def explain_concept(client, concept: str, level: str = "beginner", language: str = "ar") -> dict[str, Any]:
    """Explain a concept simply, with an analogy and a quick check question."""
    out = _gen(
        client,
        "You are a patient tutor. Explain the concept simply for the given level, include one "
        "relatable analogy, and end with one quick check-for-understanding question.",
        f"Language: {language}.\nConcept: {concept}\nLevel: {level}",
    )
    return {"concept": concept, "explanation": out}


# =========================================================================== #
# Track registry
# =========================================================================== #
# name -> (callable, needs_client)
TRACK_TOOLS = {
    # healthcare
    "summarize_clinical_note": summarize_clinical_note,
    "drug_information": drug_information,
    "patient_instructions": patient_instructions,
    "symptom_triage": symptom_triage,
    # education
    "lesson_plan": lesson_plan,
    "generate_quiz": generate_quiz,
    "make_flashcards": make_flashcards,
    "explain_concept": explain_concept,
}
TRACK_TOOLS_NEED_CLIENT = set(TRACK_TOOLS.keys())  # all track tools call Fanar

HEALTHCARE_SCHEMAS = [
    {"name": "summarize_clinical_note", "description": "Summarise a clinical note into structured sections and save it.", "args": {"note": "string", "language": "'en'|'ar' (optional)"}},
    {"name": "drug_information", "description": "Explain a medication (uses, side effects, cautions). Educational only.", "args": {"name": "string", "language": "'en'|'ar' (optional)"}},
    {"name": "patient_instructions", "description": "Write patient-friendly home-care instructions and save them.", "args": {"condition": "string", "medications": "string (optional)", "language": "'en'|'ar' (optional)"}},
    {"name": "symptom_triage", "description": "Give cautious general guidance + urgency level for symptoms. Not a diagnosis.", "args": {"symptoms": "string", "language": "'en'|'ar' (optional)"}},
]

EDUCATION_SCHEMAS = [
    {"name": "lesson_plan", "description": "Create and save a structured lesson plan.", "args": {"topic": "string", "level": "string (optional)", "language": "'en'|'ar' (optional)"}},
    {"name": "generate_quiz", "description": "Generate a multiple-choice quiz with answer key and save it.", "args": {"topic": "string", "num_questions": "int (optional)", "level": "string (optional)", "language": "'en'|'ar' (optional)"}},
    {"name": "make_flashcards", "description": "Generate front/back study flashcards and save them.", "args": {"topic": "string", "num_cards": "int (optional)", "language": "'en'|'ar' (optional)"}},
    {"name": "explain_concept", "description": "Explain a concept simply with an analogy and a check question.", "args": {"concept": "string", "level": "string (optional)", "language": "'en'|'ar' (optional)"}},
]

_ALL_BROWSER = ["fanar_knowledge", "web_search", "open_page", "see_page", "click_mark", "fill_mark",
                "fill_date", "fill_date_smart", "request_credentials", "fill_login", "submit_form",
                "fill_payment_card", "read_page", "list_form_fields", "fill_field", "click",
                "pause_for_user", "generate_official_letter", "save_document"]
_ALL_DOMAIN = ["summarize_clinical_note", "drug_information", "patient_instructions", "symptom_triage",
               "lesson_plan", "generate_quiz", "make_flashcards", "explain_concept"]

TRACKS: dict[str, dict[str, Any]] = {
    "general": {
        "label": "Fanar Navigator",
        "tagline": "Your Qatar government, health & education navigator",
        "persona": "You are the FANAR Government Navigator, a single Arabic-first agentic assistant for Qatar "
                   "covering government services, healthcare support, and education & heritage. You understand "
                   "intent (including Gulf dialect), find the RIGHT official service, explain requirements in the "
                   "user's language, and ACT through a real browser (and, on desktop, the screen) to complete "
                   "tasks end-to-end — guiding the user to a review/confirmation step before anything irreversible. "
                   "KNOWLEDGE-FIRST: for general questions (documents, requirements, how a process works) answer "
                   "from fanar_knowledge first; only open the browser for LIVE or account-specific data (e.g. 'my "
                   "traffic fines', 'my visa status'). For medical guidance always add a brief safety disclaimer.",
        "tools": _ALL_BROWSER + _ALL_DOMAIN,
        "schemas_extra": HEALTHCARE_SCHEMAS + EDUCATION_SCHEMAS,
        "quick_actions": [
            "What documents do I need for a family visit visa in Qatar?",
            "Check my traffic violations",
            "تحقق من حالة تأشيرتي",
            "Guide me through how to renew my residency permit",
            "Explain Metformin and write Arabic patient instructions for hypertension",
            "Make a 5-question quiz on Qatari history and save it",
        ],
    },
    "government": {
        "label": "Smart Government",
        "tagline": "Citizen services, on autopilot",
        "persona": "You are Fanar Agent for Qatar's Smart Government & Citizen Services. You help "
                   "residents understand and COMPLETE government tasks: finding official services, "
                   "reading real .gov.qa pages, filling forms, drafting official letters.",
        "tools": ["fanar_knowledge", "web_search", "open_page", "see_page", "click_mark", "fill_mark",
                  "fill_date", "fill_date_smart", "request_credentials", "fill_login", "submit_form",
                  "read_page", "list_form_fields", "fill_field", "click", "pause_for_user",
                  "generate_official_letter", "save_document"],
        "schemas_extra": [],
        "quick_actions": [
            "Find the official Qatar ID renewal fee and required documents",
            "Open the Hukoomi portal and find how to renew a driving licence",
            "Draft an official letter requesting a salary certificate",
        ],
    },
    "healthcare": {
        "label": "Healthcare Support",
        "tagline": "Patient-first medical assistance",
        "persona": "You are Fanar Agent for Healthcare Information & Patient Support. You help "
                   "summarise clinical notes, explain medications, write patient instructions, and give "
                   "cautious triage guidance. You are NOT a doctor and you always add a safety disclaimer.",
        "tools": ["fanar_knowledge", "summarize_clinical_note", "drug_information", "patient_instructions", "symptom_triage",
                  "web_search", "open_page", "see_page", "click_mark", "fill_mark", "fill_date", "fill_date_smart",
                  "request_credentials", "fill_login", "read_page", "pause_for_user", "save_document"],
        "schemas_extra": HEALTHCARE_SCHEMAS,
        "quick_actions": [
            "Summarise this clinical note into SOAP format",
            "Explain the medication Metformin and its side effects",
            "Write Arabic patient instructions for managing high blood pressure",
        ],
    },
    "education": {
        "label": "Education & Heritage",
        "tagline": "Adaptive tutoring in Arabic & beyond",
        "persona": "You are Fanar Agent for Education & Cultural Preservation. You build lesson plans, "
                   "quizzes and flashcards, and tutor in Arabic and English, celebrating Arabic language "
                   "and heritage.",
        "tools": ["fanar_knowledge", "lesson_plan", "generate_quiz", "make_flashcards", "explain_concept",
                  "web_search", "open_page", "see_page", "click_mark", "fill_mark", "fill_date", "fill_date_smart",
                  "request_credentials", "fill_login", "read_page", "pause_for_user", "save_document"],
        "schemas_extra": EDUCATION_SCHEMAS,
        "quick_actions": [
            "Create a lesson plan on Arabic calligraphy for secondary students",
            "Generate a 5-question quiz about Qatari history",
            "Make Arabic flashcards for common medical terms",
        ],
    },
}


def get_track(track: str) -> dict[str, Any]:
    return TRACKS.get(track, TRACKS["general"])
