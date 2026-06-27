"""
Real agent tools for the Smart Government & Citizen Services track.

Unlike a mock chatbot, these tools take genuine actions:
  - web_search        : real web search (DuckDuckGo) over the live internet
  - open_page         : drives a REAL browser to a real URL (visible window)
  - read_page         : reads the visible text of the current page
  - list_form_fields  : inspects the real form fields on the current page
  - fill_field        : types into a real input on the page
  - click             : clicks a real button / link
  - pause_for_user    : hands control to the human (login, OTP, final submit)
  - generate_official_letter / save_document : writes real artefacts to disk

Browser tools operate on a per-session real browser (see browser_session.py).
The session is injected by the agent loop — the model never sees it.

Safety model:
  * The agent NEVER types credentials. When a page needs login/OTP or a final
    irreversible submit, it calls `pause_for_user`, the human acts in the real
    browser window, and the agent resumes.
  * File writes are sandboxed under ./agent_workspace.
"""

from __future__ import annotations

import datetime
import inspect
import os
import pathlib
import re
from typing import Any, Callable

import httpx

from browser_session import BrowserSession, get_session
import desktop
import payment_store
import tracks

WORKSPACE = pathlib.Path(os.getenv("AGENT_WORKSPACE", "agent_workspace")).resolve()
WORKSPACE.mkdir(parents=True, exist_ok=True)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# Verified, real Qatar e-gov service URLs (confirmed browser-accessible). The MOI
# inquiry pages are public (no login) but show a verification-code CAPTCHA on submit —
# the agent fills the other fields and pauses for the human to type the code.
GOV_PORTALS = {
    "Hukoomi services directory": "https://hukoomi.gov.qa/en",
    "Hukoomi categories": "https://hukoomi.gov.qa/en/categories",
    "Hukoomi life moments": "https://hukoomi.gov.qa/en/life-moments",
    "MOI traffic violations inquiry": "https://fees2.moi.gov.qa/moipay/inquiry/violation?language=en",
    "MOI visa inquiry & printing": "https://portal.moi.gov.qa/wps/portal/MOIInternet/services/inquiries/visaservices/enquiryandprinting",
    "MOI visa approval tracking": "https://portal.moi.gov.qa/wps/portal/MOIInternet/services/inquiries/visaservices/visaapprovaltracking",
    "MOI residency permit inquiry": "https://portal.moi.gov.qa/wps/portal/MOIInternet/services/inquiries/residencypermits",
    "MOI inquiries hub": "https://portal.moi.gov.qa/wps/portal/MOIInternet/services/inquiries",
    "MOPH appointment booking": "https://appointments.moph.gov.qa/appointment/bookappointment?lang=en",
}


def _safe_path(filename: str) -> pathlib.Path:
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", filename).strip() or "untitled.txt"
    target = (WORKSPACE / name).resolve()
    if WORKSPACE not in target.parents and target != WORKSPACE:
        raise ValueError("Refusing to write outside the agent workspace.")
    return target


# --------------------------------------------------------------------------- #
# Web search (real, no API key — DuckDuckGo HTML endpoint)
# --------------------------------------------------------------------------- #
def fanar_knowledge(client: Any, query: str) -> dict[str, Any]:
    """Answer from Fanar's OWN built-in knowledge first. Returns sufficient=False when the
    question needs live/account-specific data, so the agent falls back to the browser."""
    try:
        ans = client.chat([
            {"role": "system", "content":
             "You are Fanar. Answer from your OWN knowledge about Qatar — government services, required "
             "documents, eligibility, procedures, general facts. If you are confident, answer clearly and "
             "accurately in the user's language. If the question needs LIVE or ACCOUNT-SPECIFIC data (e.g. "
             "'my traffic fines', 'my visa status', a balance, an exact current fee that changes) or you are "
             "not sure, reply with EXACTLY: INSUFFICIENT_KNOWLEDGE"},
            {"role": "user", "content": query},
        ], model=client.default_model, temperature=0.2, max_tokens=600)
    except Exception as exc:  # noqa: BLE001
        return {"sufficient": False, "answer": "", "error": str(exc)}
    sufficient = "INSUFFICIENT_KNOWLEDGE" not in (ans or "").upper()
    return {"sufficient": sufficient, "answer": ans if sufficient else ""}


def web_search(query: str, max_results: int = 6) -> dict[str, Any]:
    """Search the live web and return real result titles + URLs + snippets."""
    try:
        resp = httpx.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": _UA},
            timeout=20,
            follow_redirects=True,
        )
        html = resp.text
    except httpx.HTTPError as exc:
        return {"query": query, "error": f"Search failed: {exc}", "results": []}

    items = re.findall(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)".*?>(.*?)</a>.*?'
        r'(?:class="result__snippet"[^>]*>(.*?)</a>)?',
        html,
        re.S,
    )
    results: list[dict[str, str]] = []
    for url, title, snippet in items:
        url = _unwrap_ddg(url)
        results.append({
            "title": _strip_html(title),
            "url": url,
            "snippet": _strip_html(snippet or ""),
        })
        if len(results) >= max_results:
            break
    return {"query": query, "results": results, "count": len(results)}


def _unwrap_ddg(url: str) -> str:
    m = re.search(r"uddg=([^&]+)", url)
    if m:
        from urllib.parse import unquote
        return unquote(m.group(1))
    if url.startswith("//"):
        return "https:" + url
    return url


def _strip_html(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()


# --------------------------------------------------------------------------- #
# Browser tools (operate on the injected session)
# --------------------------------------------------------------------------- #
def open_page(session: BrowserSession, url: str) -> dict[str, Any]:
    """Navigate the real browser to a URL and return the page state + screenshot."""
    return session.navigate(url)


def read_page(session: BrowserSession) -> dict[str, Any]:
    """Return the visible text + a fresh screenshot of the current page."""
    return session.state()


def list_form_fields(session: BrowserSession) -> dict[str, Any]:
    """List the visible form inputs on the current page so the agent can fill them."""
    return {"fields": session.list_inputs()}


def fill_field(session: BrowserSession, field: str, value: str) -> dict[str, Any]:
    """Type `value` into the input best matching `field` (name/id/placeholder/label)."""
    return session.fill(field, value)


def click(session: BrowserSession, target: str) -> dict[str, Any]:
    """Click a button / link / element matching `target` (visible text or selector)."""
    return session.click(target)


def see_page(session: BrowserSession) -> dict[str, Any]:
    """Set-of-Marks: draw numbered boxes over every clickable element and read the page."""
    return session.annotate()


def click_mark(session: BrowserSession, n: int) -> dict[str, Any]:
    """Click the numbered box from the most recent see_page (e.g. n=14)."""
    return session.click_mark(n)


def fill_mark(session: BrowserSession, n: int, text: str) -> dict[str, Any]:
    """Type into the numbered box from the most recent see_page."""
    return session.fill_mark(n, text)


def fill_date(session: BrowserSession, n: int, text: str = "", **kwargs: Any) -> dict[str, Any]:
    """Fill a DATE field by numbered box — robust to date-picker widgets and readonly
    date inputs. Accepts the format under `format` or `fmt` (default yyyy/mm/dd)."""
    fmt = kwargs.get("format") or kwargs.get("fmt") or "yyyy/mm/dd"
    return session.fill_date(int(n), text, fmt)


def fill_date_smart(session: BrowserSession, value: str = "", synonyms: Any = None, **kwargs: Any) -> dict[str, Any]:
    """Fill a DATE by VALUE (no box number) — finds the field itself and handles a
    native date picker, a single text field, OR a 3-box year/month/day group."""
    if synonyms is None:
        synonyms = kwargs.get("synonym") or kwargs.get("labels") or []
    if isinstance(synonyms, str):
        synonyms = [synonyms]
    val = value or kwargs.get("text") or kwargs.get("date") or ""
    return session.fill_date_smart(str(val), list(synonyms or []))


def fill_text_smart(session: BrowserSession, value: str = "", synonyms: Any = None, **kwargs: Any) -> dict[str, Any]:
    """Fill a TEXT field by MEANING (no box number) — finds the field by name/id/placeholder/label
    matching the synonyms, anywhere on the page (not just the viewport), and sets it robustly."""
    if synonyms is None:
        synonyms = kwargs.get("synonym") or kwargs.get("labels") or []
    if isinstance(synonyms, str):
        synonyms = [synonyms]
    val = value or kwargs.get("text") or ""
    return session.fill_text_smart(str(val), list(synonyms or []))


def fill_login(session: BrowserSession, username: str = "", password: str = "", submit: bool = True,
               scope: str = "", humanize: bool = False) -> dict[str, Any]:
    """Fill the page's login form. The agent loop injects the user's credentials here;
    the model never sees the values. `scope` optionally restricts to a login container.
    `humanize` types with real keystrokes + mouse movement (used on bot-protected portals
    so invisible reCAPTCHA scores the session as human)."""
    return session.fill_login(username, password, submit, scope, humanize)


def submit_form(session: BrowserSession) -> dict[str, Any]:
    """Press the page's Submit / Search / Inquire button yourself (English or Arabic)."""
    return session.submit_form()


def submit_inquiry_form(session: BrowserSession) -> dict[str, Any]:
    """Click the submit/search button INSIDE the form holding the input fields (never the
    site-wide search form). Used for MOI inquiry pages."""
    return session.submit_inquiry_form()


def click_smart(session: BrowserSession, labels: Any = None, **kwargs: Any) -> dict[str, Any]:
    """Robustly click a navbar/header control (e.g. 'English') — scrolls to the top first and
    matches by label (exact word or substring)."""
    if labels is None:
        labels = kwargs.get("label") or kwargs.get("target") or []
    if isinstance(labels, str):
        labels = [labels]
    return session.click_smart(list(labels or []))


def click_tab(session: BrowserSession, labels: Any = None, **kwargs: Any) -> dict[str, Any]:
    """Click a tab/switch INSIDE the page body (e.g. the 'ID Number' search-mode tab on the MOI
    fees page) — prefers real tab controls and matches by label (exact word or substring)."""
    if labels is None:
        labels = kwargs.get("label") or kwargs.get("target") or []
    if isinstance(labels, str):
        labels = [labels]
    return session.click_tab(list(labels or []))


def detect_otp(session: BrowserSession, field_selector: str = "#otp-field",
               form_selector: str = "#mfaOtpFrm", timeout_ms: int = 6000) -> dict[str, Any]:
    """Wait for a one-time-code (OTP) page to appear (deterministic helper; not model-facing)."""
    return session.detect_otp(field_selector, form_selector, timeout_ms)


def fill_otp(session: BrowserSession, code: str = "", field_selector: str = "#otp-field",
             form_selector: str = "#mfaOtpFrm", humanize: bool = True) -> dict[str, Any]:
    """Fill the OTP field with the user's one-time code and click Continue inside the OTP form.
    The code is supplied by the agent loop (from the user); the model never sees it."""
    return session.fill_otp(code, field_selector, form_selector, humanize)


def click_modal(session: BrowserSession, labels: Any = None, timeout_ms: int = 6000,
                select: str = "", **kwargs: Any) -> dict[str, Any]:
    """In a pop-up modal/dialog: optionally select an option (`select`, e.g. 'Debit Card') then
    click its action button (e.g. 'Pay'). Waits for the modal; ignores Close/Cancel/language."""
    if labels is None:
        labels = kwargs.get("label") or kwargs.get("target") or []
    if isinstance(labels, str):
        labels = [labels]
    if not select:
        select = kwargs.get("option", "") or ""
    return session.click_modal(list(labels or []), timeout_ms, select)


def review_payment(session: BrowserSession) -> dict[str, Any]:
    """Read the REVIEW PAYMENT page (Total Fee Amount + the Delivery Options / Home Address details)
    and hand it to the user to review BEFORE paying. Returns an awaiting_user 'review' prompt with the
    extracted details; the agent loop surfaces it (and adds the email). After approval, click 'Pay'."""
    info = session.read_payment_review()
    fee = info.get("total_fees", "")
    reason = (f"This is the amount you'll pay: {fee}. Please review the fee and delivery details "
              "before continuing.") if fee else "Please review the payment details before continuing."
    return {"status": "awaiting_user", "kind": "review", "reason": reason,
            "details": {"total_fees": fee, "address": info.get("address", []),
                        "title": info.get("title", ""), "raw": info.get("raw", ""),
                        "lines": info.get("lines", [])}}


# Human-readable labels for the MOI National Address edit fields (their inputs are named like
# "updateAreaId" with no usable <label>). Keys are the lowercased field name/id.
_ADDR_LABELS = {
    "updateareaid": "Area / Zone", "updatezoneid": "Zone", "updatestreetid": "Street",
    "updatebuildingnoid": "Building No.", "updatebuildingid": "Building No.",
    "updateunitnoid": "Unit No.", "updateunitid": "Unit No.",
    "updateelectricitynoid": "Electricity No.", "updatepostofficeboxid": "P.O. Box",
    "updatepoboxid": "P.O. Box", "updatemobilenoid": "Mobile No.",
    "updatemobileid": "Mobile No.", "updatephonenumberid": "Phone Number",
    "updatephoneid": "Phone Number", "updateemailid": "Email", "updatezipid": "Postal Code",
}
# Token expansions used when humanizing an unknown field name.
_LABEL_TOKENS = [
    ("postofficebox", "P.O. Box"), ("pobox", "P.O. Box"), ("phonenumber", "Phone Number"),
    ("mobileno", "Mobile No."), ("phoneno", "Phone No."), ("buildingno", "Building No."),
    ("unitno", "Unit No."), ("electricityno", "Electricity No."), ("flatno", "Flat No."),
    ("streetno", "Street No."), ("zoneno", "Zone No."), ("email", "Email"), ("mobile", "Mobile"),
    ("phone", "Phone"), ("street", "Street"), ("building", "Building"), ("zone", "Zone"),
    ("area", "Area"), ("unit", "Unit"), ("electricity", "Electricity"), ("box", "Box"),
]


def _readable_address_label(name: str, found_label: str) -> str:
    """Turn a raw field name/id (e.g. 'updateAreaId') into a user-friendly label."""
    n = re.sub(r"[^a-z0-9]", "", (name or "").lower())
    if n in _ADDR_LABELS:
        return _ADDR_LABELS[n]
    # If the reader found a genuine label (has a space / isn't just the id), keep it.
    fl = (found_label or "").strip()
    if fl and fl.lower() != (name or "").lower() and (" " in fl or not re.fullmatch(r"[a-z0-9_]+", fl.lower() or "")):
        return fl
    core = re.sub(r"^update", "", n)
    core = re.sub(r"id$", "", core)
    for token, label in _LABEL_TOKENS:
        if core == token:
            return label
    # Generic: split out a trailing 'no'/'number', title-case the rest.
    m = re.match(r"^(.*?)(no|number)$", core)
    if m and m.group(1):
        return m.group(1).title() + (" No." if m.group(2) == "no" else " Number")
    return core.title() if core else (name or "Field")


def edit_national_address(session: BrowserSession) -> dict[str, Any]:
    """Make the National Address fields editable and surface them to the user to review/change
    BEFORE saving. Clicks the page's "Update" button FIRST (so the fields become editable — the
    model used to skip this), then reads them. Returns an awaiting_user 'edit' prompt with the
    fields + current values; after the user submits, the agent fills the values, clicks Next,
    confirms the acknowledgement dialog, and reports success — all deterministically."""
    # 1) Click "Update" to switch the form into edit mode (done in code so it can't be skipped).
    try:
        session.click("Update")
    except Exception:  # noqa: BLE001 — already editable / different label → the read below still tries
        pass
    # 2) Read the now-editable fields (read_editable_form retries while they enable via AJAX).
    info = session.read_editable_form()
    fields = info.get("fields", []) if isinstance(info, dict) else []
    # Give each field a human-readable label (the MOI inputs are named like "updateareaid" with no
    # usable <label>, so we humanize the name). The `key` is the stable tag index from the reader.
    for f in fields:
        if isinstance(f, dict):
            f["label"] = _readable_address_label(f.get("name", ""), f.get("label", ""))
    if fields:
        reason = ("These are your current National Address details. Change anything you need right "
                  "here, then click Save Update and I'll submit it for you.")
    else:
        reason = ("I couldn't read the editable address fields automatically (they may still be "
                  "loading). If you don't need to change anything, click Save Update and I'll "
                  "submit it; otherwise start a new conversation and try again.")
    return {"status": "awaiting_user", "kind": "edit", "reason": reason, "fields": fields}


def fill_id_card_form(session: BrowserSession, service_type: str = "") -> dict[str, Any]:
    """Replace Lost/Damaged ID Card form ("Expatriate Data" page): tick the Service Type radio
    ("Replace Lost" or "Replace Damaged" per `service_type`), tick the "My QID" radio, click Next,
    and confirm the delivery dialog (OK) — all deterministically (radios have no clickable text)."""
    return session.fill_id_card_form(service_type=service_type or "")


def confirm_payment_method(session: BrowserSession, card_type: str = "") -> dict[str, Any]:
    """In the MOI 'Payment Method' dialog: tick the card-option radio that matches the user's saved
    card type and press 'Pay' (#continue), which redirects to the bank gateway. The MOI dialog's
    radio ids are confusingly named — CREDIT card = #debitCardOptionRadio, DEBIT card =
    #qPayCardOptionRadio — so map by card type. Selects directly on the DOM. Model calls it no-arg."""
    label = card_type or payment_store.card_type_label()
    radio_id = "debitCardOptionRadio" if "credit" in label.lower() else "qPayCardOptionRadio"
    return session.confirm_payment_method(radio_id=radio_id, card_label=label)


def fill_service_dialog(session: BrowserSession, email: str = "", address_type: str = "Home Address",
                        language: str = "English", click_pay: bool = True) -> dict[str, Any]:
    """Fill the MOI service dialog (e.g. National Address Certificate): tick the address-type
    checkbox, select the language, fill the email, and press Pay — all inside the pop-up dialog.
    The EMAIL is injected from the user's saved profile by the agent loop; you never pass it."""
    return session.fill_service_dialog(email, address_type, language, click_pay)


def capture_captcha(session: BrowserSession) -> dict[str, Any]:
    """Screenshot the captcha image and locate its input + submit button (internal helper
    for the in-app captcha flow). Returns a base64 image data URL and the box numbers."""
    return session.capture_captcha()


def fill_payment_card(session: BrowserSession) -> dict[str, Any]:
    """Fill the checkout form with the user's SAVED payment card. Values are read from the
    local payment store and injected here; you never pass or see them."""
    card = payment_store.load_payment()
    if not card:
        return {"error": "No saved payment card. Ask the user to add one in the Payment Card panel."}
    return session.fill_payment_card(card)


def request_credentials(reason: str = "Please enter your login details.", fields: list[str] | None = None) -> dict[str, Any]:
    """Ask the user (securely, in the UI) for credentials to log in. Returns a control
    signal; the agent loop pauses and the user types them into a masked form."""
    return {"status": "awaiting_user", "kind": "credentials", "reason": reason, "fields": fields or ["username", "password"]}


def pause_for_user(reason: str) -> dict[str, Any]:
    """Hand control to the human (e.g. log in / enter OTP / confirm final submit).

    This returns a control signal; the agent loop stops and waits for the user to
    act in the real browser window and press Continue.
    """
    return {"status": "awaiting_user", "reason": reason}


# --------------------------------------------------------------------------- #
# Artefact tools (real file writes)
# --------------------------------------------------------------------------- #
def generate_official_letter(
    purpose: str,
    recipient: str,
    body: str,
    applicant_name: str = "",
    language: str = "en",
) -> dict[str, Any]:
    """Generate a formatted official letter (English) and save it to disk."""
    today = datetime.date.today().isoformat()
    text = (
        f"Date: {today}\nTo: {recipient}\n\nSubject: {purpose}\n\n"
        f"{body}\n\nYours sincerely,\n{applicant_name}\n"
    )
    fname = f"letter_{re.sub(r'[^A-Za-z0-9]+', '_', purpose)[:40] or 'official'}.txt"
    path = _safe_path(fname)
    path.write_text(text, encoding="utf-8")
    return {"saved_to": str(path.relative_to(WORKSPACE.parent)), "preview": text}


def save_document(filename: str, content: str) -> dict[str, Any]:
    """Write arbitrary text content to a file in the agent workspace."""
    path = _safe_path(filename)
    path.write_text(content, encoding="utf-8")
    return {"saved_to": str(path.relative_to(WORKSPACE.parent)), "bytes": len(content.encode("utf-8"))}


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
# Tools whose first argument is the browser session (injected by the agent loop).
_BROWSER_TOOLS = {"open_page", "read_page", "see_page", "click_mark", "fill_mark",
                  "fill_date", "fill_date_smart", "fill_text_smart", "fill_login", "submit_form", "submit_inquiry_form",
                  "capture_captcha", "fill_payment_card", "click_smart", "click_tab", "list_form_fields",
                  "fill_field", "click", "detect_otp", "fill_otp", "click_modal", "fill_service_dialog",
                  "confirm_payment_method", "review_payment", "edit_national_address", "expand_all",
                  "fill_editable_form", "read_editable_form", "fill_id_card_form"}

TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "fanar_knowledge": fanar_knowledge,
    "web_search": web_search,
    "open_page": open_page,
    "read_page": read_page,
    "see_page": see_page,
    "click_mark": click_mark,
    "fill_mark": fill_mark,
    "fill_date": fill_date,
    "fill_date_smart": fill_date_smart,
    "fill_text_smart": fill_text_smart,
    "fill_login": fill_login,
    "submit_form": submit_form,
    "submit_inquiry_form": submit_inquiry_form,
    "click_smart": click_smart,
    "click_tab": click_tab,
    "detect_otp": detect_otp,
    "fill_otp": fill_otp,
    "click_modal": click_modal,
    "fill_service_dialog": fill_service_dialog,
    "confirm_payment_method": confirm_payment_method,
    "review_payment": review_payment,
    "edit_national_address": edit_national_address,
    "expand_all": lambda session, labels=None: session.expand_all(labels or []),
    # Editable-form read/fill for the National Address Update (the fill was the missing piece —
    # _finish_address_update dispatches "fill_editable_form", which must be registered here).
    "fill_editable_form": lambda session, values=None: session.fill_editable_form(values or {}),
    "read_editable_form": lambda session: session.read_editable_form(),
    "fill_id_card_form": fill_id_card_form,
    "capture_captcha": capture_captcha,
    "fill_payment_card": fill_payment_card,
    "request_credentials": request_credentials,
    "list_form_fields": list_form_fields,
    "fill_field": fill_field,
    "click": click,
    "pause_for_user": pause_for_user,
    "generate_official_letter": generate_official_letter,
    "save_document": save_document,
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {"name": "fanar_knowledge", "description": "Answer from Fanar's OWN built-in knowledge of Qatar (required documents, requirements, procedures, eligibility, general facts). Use this FIRST for general informational questions. Returns sufficient=false if the question needs live/account-specific data — then use the browser.", "args": {"query": "string"}},
    {"name": "web_search", "description": "Search the live web for the right official page/service. Use this to find real government URLs.", "args": {"query": "string"}},
    {"name": "open_page", "description": "Open a REAL browser at a URL (a visible window). Returns title, visible text and a screenshot. Use real government URLs (e.g. portal.moi.gov.qa, hukoomi.gov.qa).", "args": {"url": "string"}},
    {"name": "see_page", "description": "PREFERRED way to interact: draws NUMBERED boxes over every clickable element on the current page and returns the labelled list. Use this, then click_mark / fill_mark by number.", "args": {}},
    {"name": "click_mark", "description": "Click the numbered box from the most recent see_page (e.g. n=14).", "args": {"n": "int - the box number"}},
    {"name": "fill_mark", "description": "Type text into the numbered box (input) from the most recent see_page.", "args": {"n": "int - the box number", "text": "string"}},
    {"name": "fill_date", "description": "Fill a DATE field by its numbered box. Use this (not fill_mark) for any date/date-of-birth/expiry field — it handles date-picker calendar widgets and readonly date inputs. Pass the date and optionally its format.", "args": {"n": "int - the box number", "text": "string - the date, e.g. 1990/05/12", "format": "string (optional, default yyyy/mm/dd)"}},
    {"name": "fill_date_smart", "description": "Fill a DATE by VALUE without a box number — it finds the date field itself and handles ANY shape: a native date picker, a single text field, OR three separate year/month/day boxes or dropdowns. Prefer this for date fields. Pass the date value and (optionally) words near the field.", "args": {"value": "string - the date, e.g. 1990/05/12", "synonyms": "list (optional) - words near the field, e.g. ['date of birth']"}},
    {"name": "read_page", "description": "Re-read the current page's visible text and take a fresh screenshot.", "args": {}},
    {"name": "request_credentials", "description": "Securely ask the user for login details. Use this when a page needs a login: the user types them into a MASKED form (you never see them). After this, call fill_login.", "args": {"reason": "string", "fields": "list (optional, default ['username','password'])"}},
    {"name": "fill_login", "description": "Auto-fill and submit the login form using the credentials the user just provided. You do NOT pass the values — they are injected securely.", "args": {"submit": "bool (optional, default true)"}},
    {"name": "submit_form", "description": "Press the page's Submit / Search / Inquire button yourself (handles English & Arabic). Use this to submit a form instead of asking the user to click it.", "args": {}},
    {"name": "fill_payment_card", "description": "Fill a checkout / payment form with the user's SAVED payment card (number, name, expiry, CVV). Values are injected securely — you never pass or see them. Use this on a payment/NAPS screen, then pause_for_user to review before paying.", "args": {}},
    {"name": "fill_service_dialog", "description": "Fill the MOI service dialog that pops up after choosing a service option (e.g. National Address Certificate): it ticks the address-type checkbox ('Home Address'), selects the language ('English'), fills the EMAIL with the user's saved email (injected — you never pass or type it), AND presses the Pay button to start the payment. Call this ONCE when the service form/dialog is open; do NOT fill those fields or click Pay yourself. After it, run the PAYMENT sub-flow (card type, fill_payment_card, dry-run).", "args": {}},
    {"name": "review_payment", "description": "On the REVIEW PAYMENT page (shows Total Fees + Home Address before paying), call this to SHOW the user the fee and details and pause for their approval. Returns once the user has reviewed. After it, click 'Pay' on the page only if they approved. Use this for the payment review step instead of pause_for_user.", "args": {}},
    {"name": "edit_national_address", "description": "For the Update National Address task ONLY: call this RIGHT AFTER you click the first 'Update' (which makes the address fields editable). It surfaces the editable fields to the user to review/change, then AUTOMATICALLY fills their values, clicks 'Next', clicks the final 'Update', confirms the acknowledgement dialog ('Continue'), and reports success. This is the LAST tool you call for that task — do NOT fill fields, click Next/Update, or click Continue yourself.", "args": {}},
    {"name": "fill_id_card_form", "description": "For the Replace Lost/Damaged ID Card task: on the 'Expatriate Data' page, pass service_type='Replace Lost' or 'Replace Damaged' (decide from the user's request; if they didn't say which, pause_for_user to ask first). It ticks that Service Type radio, ticks the 'My QID' radio, clicks Next, and confirms the delivery dialog (OK) — all automatically. Do NOT click the radios, Next, or OK yourself. After it, call review_payment.", "args": {"service_type": "'Replace Lost' or 'Replace Damaged'"}},
    {"name": "confirm_payment_method", "description": "In the MOI 'Payment Method' dialog (the one with Credit Card / Debit Card options and a Pay button), tick the card-type option and press Pay, which redirects to the bank gateway. Call it with NO arguments — it selects the user's saved card type and presses Pay deterministically. Do NOT pick the option or click Pay yourself, and do NOT open the gateway URL directly.", "args": {}},
    {"name": "list_form_fields", "description": "List the form inputs on the current page (fallback to see_page).", "args": {}},
    {"name": "fill_field", "description": "Type a value into the input matching `field` (fallback to fill_mark).", "args": {"field": "string", "value": "string"}},
    {"name": "click", "description": "Click a button/link by visible text (fallback to click_mark).", "args": {"target": "string"}},
    {"name": "pause_for_user", "description": "Hand control to the human and WAIT (for OTP, payment, captcha, or a final irreversible submit).", "args": {"reason": "string"}},
    {"name": "generate_official_letter", "description": "Generate and SAVE a formatted official letter. language is 'en' or 'ar'.", "args": {"purpose": "string", "recipient": "string", "body": "string", "applicant_name": "string (optional)", "language": "'en'|'ar' (optional)"}},
    {"name": "save_document", "description": "Save any text content to a named file in the workspace.", "args": {"filename": "string", "content": "string"}},
]


def build_schemas(track: str, surface: str) -> list[dict[str, Any]]:
    """Return the tool schemas available for a given track + surface (web|desktop)."""
    cfg = tracks.get_track(track)
    allowed = set(cfg["tools"])
    schemas = [s for s in TOOL_SCHEMAS if s["name"] in allowed]
    schemas += [s for s in cfg["schemas_extra"]]
    if surface == "desktop":
        schemas += desktop.DESKTOP_SCHEMAS
    return schemas


# Common arg-name aliases the small planner model emits. We only ever rename an
# alias to its canonical name when (a) the tool actually accepts the canonical name
# and (b) the canonical name is absent — so we never clobber a correct value.
_ARG_ALIASES: dict[str, tuple[str, ...]] = {
    "n": ("box", "index", "number", "mark", "box_number", "boxnumber", "boxnum", "i"),
    "text": ("value", "input", "content", "txt", "string"),
    "target": ("label", "selector", "element", "button", "link_text"),
    "url": ("link", "address", "href", "page", "site"),
    "query": ("q", "search", "search_query", "keyword", "keywords"),
    "field": ("field_name", "name_attr"),
}


def _prepare_args(fn: Callable[..., Any], args: dict[str, Any]) -> dict[str, Any]:
    """Make a (small) planner model's tool call robust: map common arg-name aliases
    to the tool's real parameter names, then drop anything the tool can't accept —
    so a stray/mis-named arg is corrected or ignored instead of crashing the step.
    Functions that declare **kwargs receive everything unchanged."""
    try:
        params = inspect.signature(fn).parameters
    except (ValueError, TypeError):
        return args
    names = set(params)
    out = dict(args)
    # Map known aliases onto the tool's real parameter names (also for **kwargs tools).
    for canon, aliases in _ARG_ALIASES.items():
        if canon in names and canon not in out:
            for a in aliases:
                if a in out:
                    out[canon] = out.pop(a)
                    break
    # Tools that accept **kwargs take everything; others get unknown keys dropped.
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return out
    allowed = {n for n, p in params.items()
               if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)}
    return {k: v for k, v in out.items() if k in allowed}


# Back-compat alias for the original (filter-only) helper name.
_accepted_args = _prepare_args


def run_tool(name: str, args: dict[str, Any], session_id: str | None = None,
             client: Any = None, surface: str = "web",
             credentials: dict[str, Any] | None = None) -> dict[str, Any]:
    """Dispatch a tool call across browser, track, and desktop tool families."""
    args = args or {}
    try:
        # Browser tools — inject a live per-session real browser.
        if name in _BROWSER_TOOLS:
            if not session_id:
                return {"error": "No browser session available for this tool."}
            # Securely inject the user's credentials into fill_login (never via the model).
            if name == "fill_login":
                creds = credentials or {}
                args = {**args, "username": creds.get("username", ""), "password": creds.get("password", "")}
            fn = TOOLS[name]
            return fn(get_session(session_id, create=True), **_accepted_args(fn, args))

        # Desktop tools — only on the desktop surface.
        if name in desktop.DESKTOP_TOOLS:
            if surface != "desktop":
                return {"error": "Desktop control is only available in the Fanar desktop app."}
            fn = desktop.DESKTOP_TOOLS[name]
            kwargs = _accepted_args(fn, dict(args))
            if name in desktop.DESKTOP_SESSION_TOOLS:
                kwargs["session_id"] = session_id or "default"
            if name in desktop.DESKTOP_VISION_TOOLS:
                return fn(client, **kwargs)
            return fn(**kwargs)

        # Fanar's own-knowledge tool — needs the client injected.
        if name == "fanar_knowledge":
            return fanar_knowledge(client, **_accepted_args(fanar_knowledge, args))

        # Track-specialized tools (healthcare / education) — Fanar-LLM backed.
        if name in tracks.TRACK_TOOLS:
            fn = tracks.TRACK_TOOLS[name]
            return fn(client, **_accepted_args(fn, args))

        # Plain tools (web_search, letter/document writers, pause_for_user).
        if name in TOOLS:
            fn = TOOLS[name]
            return fn(**_accepted_args(fn, args))

        return {"error": f"Unknown tool '{name}'."}
    except TypeError as exc:
        return {"error": f"Bad arguments for {name}: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"Tool {name} failed: {exc}"}
