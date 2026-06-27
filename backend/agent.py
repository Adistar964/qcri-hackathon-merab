"""
The agentic orchestration loop — track-aware, multi-surface, human-in-the-loop.

Capabilities, by surface:
  • web     — drives a real browser (search, navigate, read, fill forms).
  • desktop — everything web has, PLUS real computer use: see the screen with Fanar
              vision and control the mouse/keyboard (with human approval per action).

Capabilities, by track: the persona and tool set switch between Smart Government,
Healthcare Support, and Education & Heritage (see tracks.py).

Fanar is OpenAI-API-compatible but has no native function-calling, so we use a robust
ReAct-style JSON protocol: each step Fanar emits ONE JSON object (a tool call or a
final answer); we parse it, run the real tool, feed back the observation, repeat.

Two reliability/safety mechanisms:
  1. Tool-forcing + corrective re-prompts — Fanar sometimes answers from memory or
     emits prose; we refuse a tool-less final answer and re-prompt to the JSON contract.
  2. Confirm-before-act — risky desktop actions (click/type/keys/open app) PAUSE for the
     human to approve; the loop stores the pending action and executes it only on resume.
"""

from __future__ import annotations

import datetime
import json
import re
import time
from typing import Any, Iterator

import credentials_store
import desktop
import profile_store
import tools as tools_mod
import workflows
from fanar_client import FanarClient, FanarError
from tracks import get_track

MAX_STEPS = 16


def _shortdesc(d: str) -> str:
    d = (d or "").split(". ")[0].strip()
    return d[:90]


def _system_prompt(track: str, surface: str) -> str:
    cfg = get_track(track)
    schemas = tools_mod.build_schemas(track, surface)
    # Compact tool list (names + arg names + a short description) to fit small context windows.
    tool_lines = "\n".join(
        f"- {s['name']}({', '.join(s['args'].keys())}): {_shortdesc(s['description'])}"
        for s in schemas
    )
    desktop_note = ("\nDESKTOP: to act on the screen call see_screen_marks (numbers every control), then "
                    "click_mark_screen(n); use type_text/press_keys for text.\n") if surface == "desktop" else ""
    return f"""{cfg['persona']}
You ACT using real tools and reply in English.
{desktop_note}
Reason-act loop: EVERY turn reply with ONE JSON object only (no prose/markdown), one of:
- tool call:  {{"thought":"<short>","action":"<tool>","action_input":{{...}}}}
- final answer (only after using tools): {{"thought":"<short>","final_answer":"<answer in English>"}}

Tools:
{tool_lines}

RULES:
- If an "AUTHORITATIVE WORKFLOW" playbook is present in the conversation, FOLLOW IT EXACTLY: use only
  its deep-link URL, its exact labels, and its values; do not invent URLs or extra steps. The page is
  already open for you.
- For any DATE field (date of birth, expiry date) use fill_date (NOT fill_mark) — it handles date-picker
  calendar widgets and readonly date inputs.
- First reply MUST be a tool call. For general info questions (documents/requirements/how-to) use
  fanar_knowledge FIRST; if sufficient=true, finalize with it. For LIVE/account data (my fines, my visa
  status) or sufficient=false, use the browser. Never answer from memory.
- Every external task is done in the BROWSER (e.g. email -> mail.google.com); never open a native app.
- open_page already numbers the page and returns the element list — click_mark(n)/fill_mark(n,text) by
  number; don't guess. Re-check the returned screenshot/boxes; if a step didn't work, re-plan.
- Login: call request_credentials (user types into a masked form you can't see), then fill_login. A
  recipient's address is NOT a login — it goes in the To field after login. Use pause_for_user for
  OTP/CAPTCHA/payment/final submit.
- Qatar gov inquiry pages have a verification-code (captcha) image: fill the QID/plate/visa fields,
  then pause_for_user for the user to type the code. Hukoomi (hukoomi.gov.qa/en) is best for finding
  services/requirements; MOI inquiry pages for "my fines/visa status".
- Opening a video/article page already shows it — don't click play; just finalize.

Example (knowledge): {{"thought":"general info","action":"fanar_knowledge","action_input":{{"query":"documents for a family visit visa in Qatar"}}}} → if sufficient, final_answer.
Example (live): {{"thought":"open MOI inquiry","action":"open_page","action_input":{{"url":"https://fees2.moi.gov.qa/moipay/inquiry/violation?language=en"}}}} → fill_mark the QID/plate → pause_for_user at the code.
"""


_SMALLTALK = (
    "hi", "hii", "hey", "hello", "helo", "yo", "hiya", "sup", "thanks", "thank you", "thx",
    "ok", "okay", "cool", "nice", "great", "good morning", "good evening", "good night",
    "how are you", "whats up", "what's up", "who are you", "what can you do", "help",
    # Arabic greetings / small-talk (checked on the raw message so they don't need translation).
    "مرحبا", "مرحبًا", "اهلا", "أهلا", "اهلين", "هلا", "سلام", "السلام عليكم", "صباح الخير",
    "مساء الخير", "شكرا", "شكرًا", "شكراً", "كيف حالك", "من انت", "من أنت", "مساعدة",
)


def _is_smalltalk(text: str) -> bool:
    t = (text or "").strip().lower().strip("?!.,")
    if len(t) > 40:
        return False
    return t in _SMALLTALK or any(t == g or t.startswith(g + " ") for g in _SMALLTALK)


# Synonyms for the "ID Number" search-mode tab, so click_tab finds it regardless of the exact
# wording the portal uses (the page is switched to English first).
_ID_TAB_VARIANTS = ["ID Number", "ID No", "Qatar ID", "QID", "Personal Number", "Civil ID",
                    "National ID"]


def _has_arabic(text: str) -> bool:
    """True if the text contains any Arabic-script character (used to decide whether to translate
    a prompt to English for ROUTING — the workflows/website are English)."""
    return bool(re.search(r"[؀-ۿ]", text or ""))


# Lost vs Damaged keywords (EN + AR, many surface forms) for the Replace ID Card service type.
# Arabic verbs/roots are included (فقد catches فقدت/فقدتُ/فقدان/أفقد) so we don't depend on the
# prompt being a noun or on the translation choosing the word "lost" over "misplaced".
_LOST_KW = ("lost", "misplace", "missing", "فقد", "مفقود", "ضائع", "ضاع", "ضياع",
            "اضعت", "أضعت", "اضاع", "أضاع", "خسرت")
_DAMAGED_KW = ("damage", "broken", "torn", "ruined", "cracked", "spoil", "تلف", "تالف",
               "متضرر", "ضرر", "اتلف", "أتلف", "معطوب", "مكسور", "خربان", "كسرت")


def _classify_lost_damaged(*texts: str) -> str:
    """Decide 'Replace Lost' vs 'Replace Damaged' from any of the given texts (the user's prompt,
    its English translation, and/or their typed answer). Returns "" when neither or BOTH appear
    (genuinely ambiguous → ask)."""
    blob = " ".join(t for t in texts if t).lower()
    lost = any(k in blob for k in _LOST_KW)
    damaged = any(k in blob for k in _DAMAGED_KW)
    if lost and not damaged:
        return "Replace Lost"
    if damaged and not lost:
        return "Replace Damaged"
    return ""


def _extract_json(text: str) -> dict[str, Any] | None:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
        start = text.find("{", start + 1)
    return None


def _trim(result: dict[str, Any]) -> dict[str, Any]:
    """Keep observations SMALL — the planner model (Fanar-S) has only a 4096-token window."""
    if not isinstance(result, dict):
        return result
    out = dict(result)
    out.pop("screenshot", None)  # the UI already has it; the model doesn't need the filename
    # The numbered element list is what the model needs; when present, drop the bulky page text.
    if isinstance(out.get("elements"), list):
        out["elements"] = [{"n": e.get("n"), "t": (e.get("type") or e.get("tag")),
                            "label": (e.get("label") or "")[:28]} for e in out["elements"][:22]]
        if "text" in out:
            out["text"] = (out["text"] or "")[:200]
    elif isinstance(out.get("text"), str) and len(out["text"]) > 400:
        out["text"] = out["text"][:400] + " …"
    if isinstance(out.get("understanding"), str) and len(out["understanding"]) > 400:
        out["understanding"] = out["understanding"][:400] + " …"
    return out


def _fit(messages: list[dict[str, str]], max_chars: int) -> list[dict[str, str]]:
    """Trim a message list to fit a model's context: always keep the system message, then
    keep the most RECENT messages that fit, hard-truncating the last one if needed."""
    if not messages:
        return messages
    system, rest = messages[0], messages[1:]
    kept: list[dict[str, str]] = []
    total = len(system["content"])
    for m in reversed(rest):
        if total + len(m["content"]) > max_chars and kept:
            break
        kept.append(m)
        total += len(m["content"])
    kept.reverse()
    out = [system] + kept
    if total > max_chars and len(out) > 1:
        fixed = total - len(out[-1]["content"])  # size of everything except the last message
        allowed = max_chars - fixed
        if allowed > 120:
            out[-1] = {**out[-1], "content": out[-1]["content"][: allowed - 20] + " …[truncated]"}
    return out


def _verify_note(tool: str, result: dict[str, Any]) -> str:
    """Tell the model whether the action ACTUALLY worked, so it self-corrects instead of
    blindly moving to the next page."""
    if not isinstance(result, dict):
        return ""
    if result.get("error"):
        return f" The tool ERRORED: {result['error']} — try a different element or approach."
    if result.get("page_error"):
        return (f" WARNING: the page shows an error — '{result['page_error']}'. The step did NOT "
                f"succeed; fix it or try another way. Do NOT proceed as if it worked.")
    if result.get("login_succeeded") is False:
        return (" WARNING: still on the login page — sign-in did NOT go through (wrong credentials, "
                "or an OTP/2FA/CAPTCHA step). Re-read the page; if it needs OTP/captcha call pause_for_user.")
    if result.get("changed") is False and tool in ("click_mark", "click"):
        return (" WARNING: the page did NOT change after that click — it may not have worked. "
                "Try a different box or approach; do not assume success.")
    return ""


# reCAPTCHA / bot-detection rejection signals on a sign-in result. Deliberately specific so the
# OTP screen ("verification code") does NOT match. (Pages are switched to English first.)
_RECAPTCHA_FAIL_HINTS = (
    "recaptcha", "captcha validation", "captcha failed", "validation failed",
    "verification failed", "not a robot", "are a robot", "robot check", "are human",
)


def _recaptcha_failed(result: dict[str, Any]) -> bool:
    """True if a sign-in result looks like it was rejected by (often invisible) reCAPTCHA, so we
    can hand off to a manual sign-in instead of looping on a login the bot-check keeps blocking."""
    if not isinstance(result, dict):
        return False
    blob = ((result.get("page_error") or "") + " " + (result.get("text") or "")).lower()
    return any(k in blob for k in _RECAPTCHA_FAIL_HINTS)


def _describe_action(tool: str, args: dict[str, Any]) -> str:
    if tool == "mouse_click":
        return f"Click at ({args.get('x')}, {args.get('y')})" + (" (double)" if args.get("double") else "")
    if tool == "type_text":
        return f"Type: \"{args.get('text', '')[:60]}\""
    if tool == "press_keys":
        return f"Press keys: {args.get('keys')}"
    if tool == "open_application":
        return f"Open application: {args.get('name')}"
    if tool == "scroll":
        return f"Scroll {args.get('amount')}"
    return f"{tool} {args}"


class AgentRun:
    """A resumable agent conversation bound to a track, surface, and (optional) browser session."""

    def __init__(self, session_id: str, client: FanarClient, track: str = "government",
                 surface: str = "web", lang: str = "en") -> None:
        self.session_id = session_id
        self.client = client
        self.track = track
        self.surface = surface
        # UI language for everything the agent SAYS to the user (steps, questions, answers). The
        # website itself is always driven in English (button labels we click, field values, saved
        # credentials/card data are never translated) — see _localize / _t.
        self.lang = (lang or "en").lower()
        self._tr_cache: dict[str, str] = {}   # EN -> AR memo so repeated static strings translate once
        self.messages: list[dict[str, str]] = [{"role": "system", "content": _system_prompt(track, surface)}]
        self.steps = 0
        self.awaiting = False
        self._tools_used = 0
        self._corrections = 0
        self._pending_action: tuple[str, dict[str, Any]] | None = None
        self._approved = False
        self._user_declined = False             # user cancelled/skipped a requested step → halt the task
        self._credentials: dict[str, str] = {}  # transient, in-memory only; never sent to the model or disk
        self._login_requested = False
        self._guard_checked = False              # Fanar guard vets the user's input once per turn
        self._login_url = ""                     # the page URL where login was needed (for saving creds)
        self._last_page_url = ""                  # most recent page URL seen (for credential mapping)
        self._smalltalk = False
        self._last_user = ""
        self._last_user_en = ""      # English view of the latest prompt, for routing/inference only
        # Strict-workflow state (matched from the Qatar e-services KB).
        self.workflow: dict[str, Any] | None = None     # matched workflow (action), or None
        self.workflow_values: dict[str, str] = {}        # resolved input values (profile + user)
        self._wf_started = False                          # deep-link opened + playbook injected
        self._wf_phase = "input"                          # input -> captcha -> submit -> done
        # Deterministic checkout (after the REVIEW PAYMENT gate): review_gate -> pay -> pay_finish
        # -> pay_continue -> pay_report -> done. _pay_last holds the most recent payment-step result.
        self._pay_last: dict[str, Any] = {}
        # Deterministic National Address Update (after the first "Update" makes fields editable):
        # address_edit (surface fields) -> address_submit (fill + Next + Update + Continue + verify).
        self._edit_values: dict[str, str] = {}
        # Deterministic Replace Lost/Damaged ID Card form (auto-filled the instant it appears).
        self._id_card_done = False
        self._id_card_service = ""
        self._do_login = False                            # deterministic fill_login pending (creds just given)
        self._post_login_url = ""                         # the page URL right after a successful sign-in
        # In-app captcha: the image is shown in the UI, the user types the code, and WE fill +
        # submit it ourselves (no more "go solve it in the browser window").
        self._captcha_code = ""                           # the code the user typed (transient)
        self._captcha_input_mark: int | None = None       # box number of the captcha code field
        self._captcha_submit_mark: int | None = None      # box number of the Submit/Search button
        self._otp_code = ""                               # one-time code the user typed in-app (transient)
        # Stop button: cooperative cancellation checked between steps.
        self._cancelled = False
        # Audit trail: the full transcript of everything done in this conversation.
        self.created_at = datetime.datetime.now().isoformat(timespec="seconds")
        self.title = ""                                   # first user prompt (conversation label)
        self.transcript: list[dict[str, Any]] = []        # every event + prompt, with timestamps

    def cancel(self) -> None:
        """Stop button: forcefully halt the run at the next checkpoint."""
        self._cancelled = True

    def record(self, event: dict[str, Any]) -> None:
        """Append an event to the audit transcript with a timestamp (called by the API)."""
        self.transcript.append({**event, "ts": datetime.datetime.now().isoformat(timespec="seconds")})

    def add_user_message(self, message: str, history: list[dict[str, str]] | None = None) -> None:
        if len(self.messages) == 1:
            for turn in history or []:
                if turn.get("role") in ("user", "assistant") and turn.get("content"):
                    self.messages.append({"role": turn["role"], "content": turn["content"]})
        self._last_user = message
        # An ENGLISH view of the request for ROUTING ONLY (workflow matching, small-talk detection,
        # lost/damaged inference) — the website + intents are English, so an Arabic prompt is
        # translated here once. The user still sees Arabic answers (those use self._last_user / the
        # Arabic _lang_instr). Falls back to the raw message if translation is unavailable.
        self._last_user_en = message
        if _has_arabic(message):
            try:
                en = self.client.translate(message, target="en")
                if en and en.strip():
                    self._last_user_en = en
            except Exception:  # noqa: BLE001 — routing must never break on a translate failure
                pass
        self._smalltalk = _is_smalltalk(self._last_user_en) or _is_smalltalk(message)
        if not self.title:
            self.title = message.strip()[:120]
        self.record({"type": "prompt", "content": message})
        self._cancelled = False
        # A new task — re-evaluate whether it matches a strict workflow.
        self.workflow = None
        self.workflow_values = {}
        self._wf_started = False
        self._wf_phase = "input"
        if not self._smalltalk:
            # Match on the English view first (covers Arabic via translation + the English
            # pay-verb boost); fall back to the raw message against the Arabic intents.
            task = workflows.match_workflow(self._last_user_en)
            if task is None and self._last_user_en != message:
                task = workflows.match_workflow(message)
            if task:
                self.workflow = task
                # Pre-fill known inputs from the user's saved info (profile-first).
                profile = profile_store.load_profile()
                for spec in workflows.required_inputs(task):
                    val = profile.get(spec["profile"], "")
                    if val:
                        self.workflow_values[spec["key"]] = val
                if profile.get("patientID"):
                    self.workflow_values["patientID"] = profile["patientID"]
        if self._smalltalk:
            # Greeting / small-talk: no tool needed — answered directly in run().
            self.messages.append({"role": "user", "content": message})
        else:
            self.messages.append({
                "role": "user",
                "content": f"{message}\n\n(Respond with ONE JSON object. Begin by using a tool — do not answer from memory.)",
            })
        self.awaiting = False
        self._tools_used = 0
        self._corrections = 0
        self._pending_action = None
        self._login_requested = False
        self._guard_checked = False     # run the Fanar guard once per user turn (in run())
        self._id_card_done = False
        self._id_card_service = ""

    def resume(self, note: str) -> None:
        """Continue after a human-in-the-loop pause. If the user DECLINES / cancels / skips the
        requested step (OTP, review, confirm, credentials, …), HALT the task — they're likely not
        interested — and run() will emit a short closing message instead of trying another step."""
        low = (note or "").strip().lower()
        declined = any(w in low for w in ("no", "don't", "dont", "cancel", "reject", "stop",
                                          "skip", "decline", "abort"))
        self._approved = not declined
        self.awaiting = False
        if declined:
            self._user_declined = True
            self._pending_action = None
            self.workflow = None
            self._wf_phase = "done"
            self._do_login = False
            return
        # The user solved the verification code → move to the deterministic submit phase.
        if self.workflow and self._wf_phase == "captcha":
            self._wf_phase = "submit"
        # Payment gates → drive the checkout DETERMINISTICALLY (the model used to skip clicking
        # "Pay" on the review page, so the Payment Method modal never opened, then it hallucinated
        # tools / clicked the gateway buttons too early). run() picks up these phases.
        if self.workflow and self._wf_phase == "review_gate":
            self._wf_phase = "pay"             # review-page Pay → Payment Method modal → gateway
            return
        if self.workflow and self._wf_phase == "pay_finish":
            self._wf_phase = "pay_continue"    # final confirmation given → press "Continue"
            return
        if self.workflow and self._wf_phase == "pay_report":
            return                             # user finished 3-D Secure → run() reads + reports
        self.messages.append({
            "role": "user",
            "content": f"The user approved/completed the step{(': ' + note) if note else ''}. Continue with the next JSON step.",
        })

    def provide_inputs(self, values: dict[str, str], save_keys: list[str] | None = None) -> None:
        """The user supplied missing form inputs (e.g. QID / DOB) for the active workflow.
        Store them for this run, optionally remember the chosen ones in the saved profile,
        then continue (run() will open the deep link and follow the playbook)."""
        # User answered the "lost or damaged?" question for the ID-card replacement → fill the form.
        # Understand the answer in English OR Arabic (مفقود/فقدت = lost, تالف/متضرر = damaged); if
        # it's still unclear, fall back to whatever the original prompt implied.
        if self.workflow and self._wf_phase == "id_card_ask":
            v = (values or {}).get("service_type", "") or ""
            self._id_card_service = (_classify_lost_damaged(v)
                                     or _classify_lost_damaged(self._last_user_en, self._last_user))
            self._wf_phase = "id_card_fill"
            self.awaiting = False
            return
        # National Address edit form submitted → fill the user's reviewed values into the page,
        # then run the rest of the Update deterministically (see _finish_address_update).
        if self.workflow and self._wf_phase == "address_edit":
            self._edit_values = {k: (v or "").strip() for k, v in (values or {}).items() if (v or "").strip()}
            self._wf_phase = "address_submit"
            self.awaiting = False
            return
        save = set(save_keys or [])
        specs = {s["key"]: s for s in workflows.required_inputs(self.workflow or {})}
        to_remember: dict[str, str] = {}
        for key, raw in (values or {}).items():
            val = (raw or "").strip()
            if not val:
                continue
            self.workflow_values[key] = val
            if key in save and key in specs:
                to_remember[specs[key]["profile"]] = val
        if to_remember:
            profile_store.update_profile(to_remember)
        self.awaiting = False

    def set_credentials(self, creds: dict[str, str], remember: bool = False) -> None:
        """Store user-entered credentials transiently (memory only). The login is then run
        DETERMINISTICALLY by run() (see _do_fill_login) — we no longer depend on the small
        planner model remembering to call fill_login, which is why logins used to fail.

        If `remember` is set, also persist the login to the local credentials store mapped to
        the site (like a password manager) so we sign in automatically next time."""
        self._credentials.update({k: v for k, v in (creds or {}).items() if v})
        self.awaiting = False
        self._do_login = True
        if remember and self._login_url and self._credentials.get("username"):
            try:
                credentials_store.save(self._login_url, self._credentials.get("username", ""),
                                       self._credentials.get("password", ""))
            except Exception:  # noqa: BLE001 — saving must never break the run
                pass
        self.messages.append({
            "role": "user",
            "content": "The user has securely provided their login details (hidden from you). "
                       "I am signing them in now; after that, continue with the task — do not log in again.",
        })

    def _use_saved_login(self, url: str) -> bool:
        """If a login is saved for this site, load it into memory and queue a deterministic
        sign-in (no prompt). Returns True if a saved login was applied."""
        if self._credentials:
            return False
        saved = None
        try:
            saved = credentials_store.get_for_url(url)
        except Exception:  # noqa: BLE001
            saved = None
        if not saved:
            return False
        self._credentials.update({"username": saved.get("username", ""),
                                  "password": saved.get("password", "")})
        self._do_login = True
        self._login_requested = True
        self._login_url = url
        return True

    def provide_captcha(self, code: str) -> None:
        """The user typed the verification code shown in the app. Store it; run() will fill it
        into the page's captcha box, press Submit itself, then read the result."""
        self._captcha_code = (code or "").strip()
        self.awaiting = False
        if self._captcha_code:
            self._wf_phase = "submit"
        else:                       # cancelled / empty → halt the task with a closing message
            self.workflow = None
            self._wf_phase = "done"
            self._user_declined = True

    def provide_otp(self, code: str) -> None:
        """The user typed the one-time code (OTP) shown in the app. Store it; run() will type it
        into the OTP field, press Continue inside the OTP form, then continue with navigation."""
        self._otp_code = (code or "").strip()
        self.awaiting = False
        if self._otp_code:
            self._wf_phase = "otp_fill"
        else:                       # cancelled / empty → halt the task with a closing message
            self._wf_phase = "done"
            self._user_declined = True

    def _browser_is_cdp(self) -> bool:
        """True when the browser is attached to the user's OWN Chrome over CDP (which can pass
        score-based reCAPTCHA Enterprise). Used to give the right guidance when login is blocked."""
        try:
            sess = tools_mod.get_session(self.session_id, create=False)
            return bool(sess and getattr(sess, "cdp", False))
        except Exception:  # noqa: BLE001
            return False

    def _dispatch(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        # On bot-protected portals, ALWAYS type the login human-like — even if the model (not the
        # deterministic path) is the one calling fill_login — so an invisible reCAPTCHA passes.
        if (tool == "fill_login" and "humanize" not in args and self.workflow
                and (self.workflow.get("human_login") or self.workflow.get("login_flow"))):
            args = {**args, "humanize": True}
        # Inject the user's saved email into the service dialog (the model must NEVER type it — it
        # used to fill the literal placeholder "<EMAIL>"). Source: workflow value, else profile.
        if tool == "fill_service_dialog" and not args.get("email"):
            email = self.workflow_values.get("email") or ""
            if not email:
                try:
                    email = profile_store.load_profile().get("email", "")
                except Exception:  # noqa: BLE001
                    email = ""
            args = {**args, "email": email}
        # Replace Lost/Damaged ID Card: infer the Service Type from the user's request if the model
        # didn't pass it, so the right radio gets selected (the user usually says "lost" or "damaged";
        # use the English view so Arabic "تالفة"/"مفقودة" route correctly too).
        if tool == "fill_id_card_form" and not args.get("service_type"):
            stype = self._infer_service_type()
            if stype:
                args = {**args, "service_type": stype}
        return tools_mod.run_tool(tool, args, session_id=self.session_id, client=self.client,
                                  surface=self.surface, credentials=self._credentials)

    # ------------------------------------------------------------------ #
    # Localisation — speak to the user in their chosen language (Arabic),
    # while the WEBSITE itself is always driven in English.
    # ------------------------------------------------------------------ #
    def _t(self, text: str) -> str:
        """Translate a short user-facing string to the UI language (cached). No-op in English, or
        for empty / purely numeric strings. Used for hardcoded step/question/closing text — model-
        generated answers are produced directly in Arabic via _lang_instr instead."""
        s = (text or "")
        if self.lang != "ar" or not s.strip():
            return text
        if not re.search(r"[A-Za-z]", s):   # no Latin text (numbers/symbols/already-Arabic) → skip
            return text
        if s in self._tr_cache:
            return self._tr_cache[s]
        out = self.client.translate(s, target="ar")
        self._tr_cache[s] = out
        return out

    def _service_unavailable_msg(self) -> str:
        """A clear, localized message for when a Fanar call fails (e.g. the chat endpoint is down or
        timing out) — so the user sees WHY nothing came back instead of an endless spinner."""
        return self._t("⚠️ Fanar (the AI service) isn't responding right now — this is usually "
                       "temporary. Please try again in a moment.")

    def _lang_instr(self) -> str:
        """A one-line directive appended to final-answer prompts so the model WRITES in the UI
        language natively (keeps streaming intact — we never translate token-by-token deltas)."""
        if self.lang == "ar":
            return (" Write your entire reply in Modern Standard Arabic (العربية). Keep numbers, "
                    "currency, emails, URLs, reference numbers and proper/portal names (e.g. Metrash2, "
                    "NAPS, QPAY) as-is. Keep any Markdown formatting.")
        return ""

    def _localize(self, ev: dict[str, Any]) -> dict[str, Any]:
        """Translate ONLY the discrete user-facing fields of an event into the UI language. Streamed
        model output (delta) and finals are produced in Arabic at the source, so they're left alone;
        tool names, action inputs (the English buttons we click) and data values are never touched."""
        if self.lang != "ar" or not isinstance(ev, dict):
            return ev
        t = ev.get("type")
        if t == "thought":
            ev = {**ev, "content": self._t(ev.get("content", ""))}
        elif t in ("stopped", "error"):
            ev = {**ev, "content": self._t(ev.get("content", ""))}
        elif t == "awaiting_user":
            ev = dict(ev)
            if ev.get("reason"):
                ev["reason"] = self._t(ev["reason"])
            # Field prompts: translate the human label + placeholder/hint, keep the machine
            # key/value (and the user's typed value) as-is.
            flds = ev.get("fields")
            if isinstance(flds, list):
                def _loc_field(f):
                    if not isinstance(f, dict):
                        return f
                    g = {**f, "label": self._t(f.get("label") or f.get("name") or f.get("key", ""))}
                    if f.get("placeholder"):
                        g["placeholder"] = self._t(f["placeholder"])
                    return g
                ev["fields"] = [_loc_field(f) for f in flds]
            # Review details: translate the address-row LABELS only (values/fee/email stay English).
            det = ev.get("details")
            if isinstance(det, dict) and isinstance(det.get("address"), list):
                det = dict(det)
                det["address"] = [
                    ({**r, "label": self._t(r.get("label", ""))} if isinstance(r, dict) else r)
                    for r in det["address"]]
                if det.get("title"):
                    det["title"] = self._t(det["title"])
                ev["details"] = det
        return ev

    def run(self) -> Iterator[dict[str, Any]]:
        """Public entry point: run the agent and localise every emitted event to the UI language."""
        for ev in self._run_impl():
            yield self._localize(ev)

    def _run_impl(self) -> Iterator[dict[str, Any]]:
        if self._cancelled:
            yield {"type": "stopped", "content": "Stopped by user."}
            return

        # The user cancelled/skipped a requested step (OTP, review, confirm, credentials, …) → they
        # are likely not interested, so HALT the task here with a short closing message.
        if self._user_declined:
            self._user_declined = False
            self.awaiting = False
            msg = self._t("No problem — I've stopped here since you cancelled that step. Nothing was "
                          "submitted. Just let me know whenever you'd like to try again or need anything else.")
            yield {"type": "delta", "content": msg}
            yield {"type": "final", "content": msg}
            return

        # Fanar GUARD: vet the user's input for safety/appropriateness BEFORE doing any work
        # (only once per turn, only on the fresh message — not on resumes/gate continuations).
        if (not self._guard_checked and not self._pending_action and not self.awaiting
                and self._last_user):
            self._guard_checked = True
            verdict = self.client.guard(self._last_user)
            if not verdict.get("safe", True):
                self.workflow = None
                self._wf_phase = "done"
                msg = self._t("I can't help with that request. I'm here to help with Qatar government "
                              "services, healthcare, and education — let me know how I can assist with "
                              "any of those and I'll get right on it.")
                yield {"type": "delta", "content": msg}
                yield {"type": "final", "content": msg}
                return

        # Greeting / small-talk → reply directly, no tools, no browser.
        if self.steps == 0 and self._smalltalk and not self._pending_action:
            yield from self._stream_smalltalk()
            return

        # Strict-workflow bootstrap: a matched Qatar e-service runs deterministically —
        # ask for any missing inputs (profile-first), open the EXACT deep link, then
        # auto-fill the fields. Read-only inquiries PAUSE here for the verification code.
        if self.workflow and not self._wf_started and not self._pending_action:
            missing = self._missing_inputs()
            if missing:
                self.awaiting = True
                yield {"type": "awaiting_user",
                       "reason": f"To do this on {self.workflow['service_name']}, I need a couple of details.",
                       "kind": "info", "fields": missing}
                return
            yield from self._start_workflow()
            if self.awaiting:          # paused at the captcha gate — stop here, resume later
                return
            # otherwise (no captcha) fall through into the reasoning loop, guided by the playbook

        # The user solved the verification code → submit + read the result deterministically.
        if self.workflow and self._wf_phase == "submit":
            yield from self._submit_and_extract()
            return

        # The user provided the OTP in-app → type it into #otp-field, press Continue inside the OTP
        # form, then CONTINUE into the model-driven navigation (don't return — there are still
        # NAVIGATE steps to reach the requested e-service).
        if self.workflow and self._wf_phase == "otp_fill":
            yield from self._fill_otp_and_continue()
            self._wf_phase = ""
            if self.awaiting:
                return
            # else fall through into the reasoning loop to complete the NAVIGATE steps

        # The user approved the REVIEW PAYMENT gate → run the rest of the checkout
        # DETERMINISTICALLY: click "Pay" on the review page, select the saved card in the Payment
        # Method modal and press Pay, then NAPS → Proceed → fill the card, and pause for a final
        # confirmation before pressing "Continue". (The model used to skip the review-page Pay.)
        if self.workflow and self._wf_phase == "pay":
            yield from self._run_payment()
            return

        # The user gave the final payment confirmation → press "Continue" and report the result.
        if self.workflow and self._wf_phase == "pay_continue":
            yield from self._finish_payment()
            return

        # The user finished a bank card-verification (3-D Secure / OTP) step → read where we
        # landed and report whether the payment went through.
        if self.workflow and self._wf_phase == "pay_report":
            yield from self._report_payment()
            return

        # The user reviewed/changed the National Address fields → write them, then finish the
        # Update DETERMINISTICALLY (Next → Update → confirm dialog → verify success).
        if self.workflow and self._wf_phase == "address_submit":
            yield from self._finish_address_update()
            return

        # The user answered "lost or damaged?" → fill the ID-card form deterministically.
        if self.workflow and self._wf_phase == "id_card_fill":
            yield from self._fill_id_card_and_review()
            return

        # If we paused for desktop-action approval and the user approved, run it now.
        if self._pending_action and self._approved:
            tool, args = self._pending_action
            self._pending_action = None
            result = self._dispatch(tool, args)
            if isinstance(result, dict) and result.get("screenshot"):
                yield {"type": "screenshot", "url": result["screenshot"], "title": _describe_action(tool, args)}
            yield {"type": "observation", "tool": tool, "result": _trim(result)}
            self.messages.append({"role": "user", "content": f"Observation from {tool}: {json.dumps(_trim(result), ensure_ascii=False)}\nContinue."})

        # The user just provided credentials → sign in DETERMINISTICALLY (don't trust the small
        # planner to remember to call fill_login). This is the key login fix.
        if self._credentials and self._do_login and not self._pending_action:
            self._do_login = False
            yield from self._do_fill_login()
            if self.awaiting:          # login needs a captcha the human solves → stop here
                return
            # After signing in, drive PHCC tasks deterministically (navigate to the record page,
            # click the section, read+extract). E-Services (login_flow → OTP + multi-step
            # navigation) instead falls into the model loop, guided by the playbook.
            if (self.workflow and self.workflow.get("requires_login")
                    and not self.workflow.get("login_flow")):
                yield from self._after_login()
                if self._wf_phase == "done":
                    return
                # else (e.g. a write task) fall into the reasoning loop with the playbook

        # Inside a strict workflow, plan with the QUALITY model (better at following the
        # playbook). 32k-token window → keep much more context so it doesn't forget the goal.
        planner = self.client.default_model if self.workflow else self.client.planner_model
        budget = 24000 if self.workflow else 7000

        while self.steps < MAX_STEPS:
            if self._cancelled:
                yield {"type": "stopped", "content": "Stopped by user."}
                return
            self.steps += 1
            self._compact()  # keep context small/fast

            # Replace Lost/Damaged ID Card: the instant the "Expatriate Data" form is on screen,
            # fill it DETERMINISTICALLY (robust fallback in case the form loaded after a prior step
            # or the model tried a bogus tool). Reads the page each tick only until the form is done.
            if (self.workflow and self.workflow.get("deterministic_form") == "id_card"
                    and not self._id_card_done):
                chk = self._dispatch("read_page", {})
                low = (chk.get("text") or "").lower() if isinstance(chk, dict) else ""
                if "service type" in low and ("qid option" in low or "replace lost" in low or "replace damaged" in low):
                    yield from self._fill_id_card_and_review()
                    if self.awaiting:
                        return
                    continue

            try:
                # The planner (Fanar-S) has a 4096-token window, so we hard-budget the context.
                raw = self.client.chat(_fit(self.messages, budget), model=planner,
                                       temperature=0.1, max_tokens=300)
            except FanarError as exc:
                # Likely a context-overflow; retry once with an aggressively trimmed context.
                if any(k in str(exc).lower() for k in ("too_large", "context", "413", "maximum")):
                    try:
                        raw = self.client.chat(_fit(self.messages, 4500), model=planner,
                                               temperature=0.1, max_tokens=256)
                    except FanarError as exc2:
                        yield {"type": "delta", "content": self._service_unavailable_msg()}
                        yield {"type": "final", "content": self._service_unavailable_msg()}
                        return
                else:
                    # Fanar is unreachable / timing out (e.g. its chat endpoint is down) → tell the
                    # user clearly instead of leaving the request hanging with no reply.
                    yield {"type": "delta", "content": self._service_unavailable_msg()}
                    yield {"type": "final", "content": self._service_unavailable_msg()}
                    return

            decision = _extract_json(raw)
            if decision is None or not (decision.get("action") or "final_answer" in decision):
                if self._corrections < 3:
                    self._corrections += 1
                    self.messages.append({"role": "assistant", "content": raw.strip()[:600]})
                    self.messages.append({"role": "user", "content": "Invalid. Reply with ONLY one JSON object (tool call or final_answer). Now."})
                    continue
                yield {"type": "final", "content": (raw or "").strip() or self._t("I could not complete the request.")}
                return

            if decision.get("thought"):
                yield {"type": "thought", "content": str(decision["thought"])}

            if "final_answer" in decision and "action" not in decision:
                if self._tools_used == 0 and self._corrections < 3:
                    self._corrections += 1
                    self.messages.append({"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)})
                    self.messages.append({"role": "user", "content": "Do not answer from memory. Use a tool first (fanar_knowledge for general questions, or the browser for live data), then answer. Tool-call JSON now."})
                    continue
                # Inside a read-only inquiry, GROUND the final answer in the live page text (not the
                # model's memory) so it can't drift into an unrelated, hallucinated answer.
                if self.workflow and self.workflow.get("read_only", True):
                    grounded = self._dispatch("read_page", {})
                    if isinstance(grounded, dict) and grounded.get("screenshot"):
                        yield {"type": "screenshot", "url": grounded["screenshot"],
                               "page_url": grounded.get("url", ""), "title": grounded.get("title", "")}
                    yield from self._extract_final(grounded.get("text", "") if isinstance(grounded, dict) else "")
                    return
                # Write the final answer with the QUALITY model, streamed to the user.
                yield from self._stream_final(str(decision["final_answer"]))
                return

            tool = decision.get("action")
            args = decision.get("action_input") or {}
            if not isinstance(args, dict):
                args = {}
            if not tool:
                yield {"type": "final", "content": raw.strip()}
                return

            # GUARD: never let the model re-open a portal/gateway URL inside an authenticated
            # workflow. MOI E-Services errors out ("already logged in elsewhere") and drops the
            # session if you navigate to its URL again — the page appears to "reload" and the model
            # then thinks it was logged out and re-asks for credentials. Navigate by CLICKING only.
            if (tool == "open_page" and self.workflow and self.workflow.get("login_flow")):
                tgt = str(args.get("url") or "").lower()
                if any(h in tgt for h in ("eservices.moi.gov.qa", "moi.gov.qa", "tawtheeq.gov.qa")):
                    note = ("Do NOT open or reload that URL — you are already signed in on this page "
                            "(re-opening it logs you out). Navigate by CLICKING the on-page links/"
                            "tabs/buttons instead. You are still logged in; continue the steps.")
                    yield {"type": "thought", "content": "Skipping a page reload — already signed in; navigating by clicking."}
                    self.messages.append({"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)})
                    self.messages.append({"role": "user", "content": f"[Blocked] {note}"})
                    self._corrections += 1
                    if self._corrections > 6:
                        yield {"type": "final", "content": self._t("I couldn't complete the navigation on the portal. Please continue in the browser window.")}
                        return
                    continue

            # GUARD: web_search is never appropriate inside an authenticated portal workflow — the
            # model reached for it to "check if I'm logged in", which derails the task.
            if (tool == "web_search" and self.workflow and self.workflow.get("login_flow")):
                yield {"type": "thought", "content": "Staying on the portal — continuing the task instead of searching the web."}
                self.messages.append({"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)})
                self.messages.append({"role": "user", "content":
                    "[Blocked] Do NOT use web_search — you are signed in on the MOI portal. Read the page "
                    "(see_page) and continue the navigation by clicking the on-page links."})
                self._corrections += 1
                if self._corrections > 6:
                    yield {"type": "final", "content": self._t("I couldn't complete the navigation on the portal. Please continue in the browser window.")}
                    return
                continue

            # GUARD: never let the model LOG ITSELF OUT mid-workflow. If it gets confused about the
            # login state it tends to click "Exit"/"Logout" — which ends the session and breaks the
            # task. Block logout/exit clicks while inside an authenticated workflow.
            if (tool in ("click", "click_smart", "click_tab", "click_modal") and self.workflow
                    and self.workflow.get("login_flow")):
                tgt = " ".join(str(v) for v in (args.get("target"), args.get("labels"), args.get("label")) if v).lower()
                if re.search(r"log\s*out|logout|sign\s*out|signout|\bexit\b|تسجيل الخروج|خروج", tgt):
                    yield {"type": "thought", "content": "Not logging out — staying signed in to finish the task."}
                    self.messages.append({"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)})
                    self.messages.append({"role": "user", "content":
                        "[Blocked] Do NOT log out / click Exit — you ARE signed in and must stay signed in to "
                        "finish this task. Continue by clicking the service navigation (e.g. 'General Services')."})
                    self._corrections += 1
                    if self._corrections > 6:
                        yield {"type": "final", "content": self._t("I couldn't complete the navigation on the portal. Please continue in the browser window.")}
                        return
                    continue

            yield {"type": "action", "tool": tool, "input": args}
            self._tools_used += 1
            self.messages.append({"role": "assistant", "content": json.dumps(decision, ensure_ascii=False)})

            # Confirm-before-act for risky desktop tools.
            if self.surface == "desktop" and tool in desktop.DESKTOP_RISKY_TOOLS:
                self._pending_action = (tool, args)
                self.awaiting = True
                self.messages.append({"role": "user", "content": f"[Paused] Awaiting user approval to: {_describe_action(tool, args)}."})
                yield {"type": "awaiting_user", "reason": f"Approve action — {_describe_action(tool, args)}?", "kind": "confirm"}
                return

            result = self._dispatch(tool, args)
            if isinstance(result, dict) and result.get("url"):
                self._last_page_url = result["url"]

            # Replace Lost/Damaged ID Card: the instant the "Expatriate Data" form appears (after the
            # model's navigation click), fill it DETERMINISTICALLY — the model kept hallucinating tools
            # like 'fill_radio' / clicking marks for the radios. This removes the model from the form.
            if (self.workflow and self.workflow.get("deterministic_form") == "id_card"
                    and not self._id_card_done and isinstance(result, dict)):
                low = (result.get("text") or "").lower()
                if "service type" in low and ("qid option" in low or "replace lost" in low or "replace damaged" in low):
                    yield {"type": "observation", "tool": tool, "result": _trim(result)}
                    yield from self._fill_id_card_and_review()
                    if self.awaiting:
                        return
                    continue

            # Website login / credentials / final-submit hand-off.
            if isinstance(result, dict) and result.get("status") == "awaiting_user":
                # The model asked for credentials — if we already have a SAVED login for this
                # site, use it automatically (sign in now) instead of prompting again.
                if result.get("kind") == "credentials":
                    self._login_url = self._last_page_url
                    if self._use_saved_login(self._last_page_url):
                        self._do_login = False
                        yield from self._do_fill_login()
                        if self.awaiting:
                            return
                        continue
                self.awaiting = True
                self.messages.append({"role": "user", "content": f"[Paused] Waiting for the user: {result.get('reason')}."})
                ev = {"type": "awaiting_user", "reason": result.get("reason", "Please complete the step."),
                      "kind": result.get("kind", "login")}
                if result.get("fields"):
                    ev["fields"] = result["fields"]
                # Pass through structured details (e.g. the payment-review fees/address) so the UI can
                # show them; add the user's email to a 'review' so it can say where it'll be sent.
                if result.get("details"):
                    det = dict(result["details"])
                    if ev["kind"] == "review":
                        det = self._structure_review(det)   # correct label↔value pairing
                    if ev["kind"] == "review" and not det.get("email"):
                        det["email"] = (self.workflow_values.get("email") or "")
                        if not det["email"]:
                            try:
                                det["email"] = profile_store.load_profile().get("email", "")
                            except Exception:  # noqa: BLE001
                                det["email"] = ""
                    ev["details"] = det
                # A payment-review gate → the NEXT resume drives the checkout deterministically
                # (see _run_payment); don't hand the payment steps back to the model loop.
                if ev["kind"] == "review":
                    self._wf_phase = "review_gate"
                # An address-edit gate → after the user submits their values, run the rest of the
                # Update deterministically (fill → Next → Update → Continue → verify).
                if ev["kind"] == "edit":
                    self._wf_phase = "address_edit"
                yield ev
                return

            if isinstance(result, dict) and result.get("screenshot"):
                yield {"type": "screenshot", "url": result["screenshot"],
                       "page_url": result.get("url", ""), "title": result.get("title", "")}

            # Deterministic auto-login: if the page has a real login form and we don't
            # have credentials yet, use a saved login if we have one, else surface the
            # secure masked form (don't rely on the model to call request_credentials).
            if (isinstance(result, dict) and result.get("has_login")
                    and not self._credentials and not self._login_requested):
                self._login_url = result.get("url") or self._last_page_url
                yield {"type": "observation", "tool": tool, "result": _trim(result)}
                if self._use_saved_login(self._login_url):
                    self._do_login = False
                    yield from self._do_fill_login()
                    if self.awaiting:
                        return
                    continue
                self._login_requested = True
                self.messages.append({"role": "user", "content":
                    f"Observation from {tool}: a login form was detected. The user is being asked for "
                    f"credentials in a secure form; once provided, call fill_login."})
                self.awaiting = True
                yield {"type": "awaiting_user",
                       "reason": "This page needs you to sign in. Enter your credentials and I'll log in for you.",
                       "kind": "credentials", "fields": ["username", "password"]}
                return

            trimmed = _trim(result)
            yield {"type": "observation", "tool": tool, "result": trimmed}
            note = _verify_note(tool, result)
            # Keep the goal in view every step so the model can't drift off-task (32k window).
            obj = (f" [TASK: {self.workflow['name']} — stay on this task; do not switch topics or "
                   f"answer unrelated questions.]" if self.workflow else "")
            self.messages.append({"role": "user", "content":
                f"Observation from {tool}: {json.dumps(trimmed, ensure_ascii=False)}.{note}\n"
                f"Verify it worked before moving on, then give the next JSON step.{obj}"})

        yield {"type": "final", "content": self._t("Reached the maximum number of steps. Please refine the request if anything is incomplete.")}


    def _missing_inputs(self) -> list[dict[str, Any]]:
        """Inputs the active workflow needs that aren't in the profile or already given."""
        out: list[dict[str, Any]] = []
        for spec in workflows.required_inputs(self.workflow or {}):
            if not self.workflow_values.get(spec["key"]):
                out.append({k: spec[k] for k in ("key", "label", "type", "format", "placeholder")
                            if spec.get(k)})
        return out

    def _start_workflow(self) -> Iterator[dict[str, Any]]:
        """Open the exact deep-link page deterministically, then inject the playbook."""
        task = self.workflow
        self._wf_started = True
        has_login = False
        yield {"type": "thought",
               "content": f"Following the verified {task['service_name']} · {task['name']} workflow."}

        # Resolve the deep link. A templated URL (e.g. PHCC /person/{patientID}/) needs a
        # patientID we only get AFTER login → open the portal LOGIN url first (it redirects to
        # the sign-in form); we navigate to the real record page once signed in.
        url = workflows.resolve_url(task, self.workflow_values)
        if "{" in url:
            url = task.get("login_url") or task.get("landing") or url.split("{")[0]

        if url:
            yield {"type": "action", "tool": "open_page", "input": {"url": url}}
            try:
                result = self._dispatch("open_page", {"url": url})
            except Exception as exc:  # noqa: BLE001 — never crash the run on a nav hiccup
                result = {"error": f"Could not open the page: {exc}"}
            self._tools_used += 1
            self.messages.append({"role": "assistant", "content": json.dumps(
                {"thought": "open the official page", "action": "open_page",
                 "action_input": {"url": url}}, ensure_ascii=False)})
            if isinstance(result, dict) and result.get("screenshot"):
                yield {"type": "screenshot", "url": result["screenshot"],
                       "page_url": result.get("url", ""), "title": result.get("title", "")}
            trimmed = _trim(result)
            yield {"type": "observation", "tool": "open_page", "result": trimmed}
            self.messages.append({"role": "user",
                                  "content": f"Observation from open_page: {json.dumps(trimmed, ensure_ascii=False)}"})
            has_login = bool(isinstance(result, dict) and result.get("has_login"))

            # Best-effort pre-clicks BEFORE filling — e.g. switch the page to English. Uses a
            # robust navbar click that scrolls to the TOP first (some pages open scrolled down to
            # the form, so the language link in the navbar can't be located otherwise). Ignored if
            # the link isn't present (page already English). The page re-renders, so we adopt the
            # refreshed element list; autofill then auto-scrolls back down to the form fields.
            for label in task.get("pre_clicks", []):
                variants = [label] + (["EN"] if label.lower() == "english" else [])
                yield {"type": "action", "tool": "click_smart", "input": {"target": label}}
                try:
                    pre = self._dispatch("click_smart", {"labels": variants})
                except Exception as exc:  # noqa: BLE001
                    pre = {"error": str(exc)}
                self._tools_used += 1
                if isinstance(pre, dict) and pre.get("screenshot"):
                    yield {"type": "screenshot", "url": pre["screenshot"],
                           "page_url": pre.get("url", ""), "title": pre.get("title", "")}
                yield {"type": "observation", "tool": "click_smart", "result": _trim(pre)}
                if isinstance(pre, dict) and pre.get("clicked") and pre.get("elements"):
                    result = pre          # adopt the refreshed (English) page state for autofill

            # In-page TABS that select the search mode BEFORE filling (e.g. the "ID Number" tab on
            # the MOI fees page — the form defaults to a different tab). Unlike pre_clicks these are
            # in the page body, not the navbar, so we use click_tab (which targets real tab controls
            # and does NOT scroll to the top). Clicking a tab swaps in its own fields → adopt them.
            for label in task.get("form_tabs", []):
                variants = [label] + (_ID_TAB_VARIANTS if "id" in label.lower() else [])
                yield {"type": "action", "tool": "click_tab", "input": {"target": label}}
                try:
                    tab = self._dispatch("click_tab", {"labels": variants})
                except Exception as exc:  # noqa: BLE001
                    tab = {"error": str(exc)}
                self._tools_used += 1
                if isinstance(tab, dict) and tab.get("screenshot"):
                    yield {"type": "screenshot", "url": tab["screenshot"],
                           "page_url": tab.get("url", ""), "title": tab.get("title", "")}
                yield {"type": "observation", "tool": "click_tab", "result": _trim(tab)}
                if isinstance(tab, dict) and tab.get("clicked") and tab.get("elements"):
                    result = tab          # adopt the tab's revealed fields for autofill

            # Deterministically fill the known inputs (QID / DOB / …) by matching the page's
            # fields semantically in English AND Arabic — the small planner can't reliably
            # pick numbered boxes, so code does the filling; the model handles the rest.
            elements = result.get("elements") if isinstance(result, dict) else None
            yield from self._autofill_workflow_inputs(elements)

        # Read-only inquiry with a verification code: capture the captcha image, show it IN THE
        # APP, and pause for the user to type the code — then WE fill it + press Submit and read
        # the result. (Degrades to the manual "solve it in the browser" flow if no image found.)
        if "captcha" in task.get("gates", []) and task.get("read_only", True):
            self._wf_phase = "captcha"
            self.awaiting = True
            cap = {}
            try:
                cap = self._dispatch("capture_captcha", {})
            except Exception:  # noqa: BLE001
                cap = {}
            image = cap.get("image") if isinstance(cap, dict) else ""
            if isinstance(cap, dict):
                self._captcha_input_mark = cap.get("input_mark")
                self._captcha_submit_mark = cap.get("submit_mark")
            ev: dict[str, Any] = {"type": "awaiting_user", "kind": "captcha"}
            if image:
                ev["image"] = image
                ev["reason"] = ("Type the verification code shown below — I'll enter it and "
                                "run the inquiry for you.")
            else:
                ev["reason"] = ("Your details are filled in. In the browser window, type the verification code "
                                "and press the Search / Submit button, then click Continue — I'll "
                                "read the result for you.")
            yield ev
            return

        # Login-gated workflow on a sign-in page → use a SAVED login if we have one, else request
        # credentials deterministically NOW (before the model acts, so it can't fire an empty
        # fill_login). After sign-in, run() signs in via _do_fill_login, then _after_login.
        # Multi-step logins (Tawtheeq + OTP) are driven by the model via the playbook instead.
        if task.get("requires_login") and has_login and not self._credentials and not task.get("login_flow"):
            login_url = (result.get("url") if isinstance(result, dict) else "") or url
            self._login_url = login_url
            used = self._use_saved_login(login_url)
            # Only non-deterministic tasks need the LLM playbook; direct_read/section tasks are
            # driven by code after login.
            if not (task.get("direct_read") or task.get("section_path")):
                playbook = workflows.build_playbook(task, self.workflow_values)
                self.messages.append({"role": "user", "content":
                    playbook + "\n\nThe sign-in page is open. After I sign the user in, continue with the "
                               "remaining steps and read the requested data. STAY ON THIS TASK."})
            if not used:
                self._login_requested = True
                self.awaiting = True
                yield {"type": "awaiting_user",
                       "reason": "This service needs you to sign in. Enter your credentials and I'll log in for you.",
                       "kind": "credentials", "fields": ["username", "password"]}
            return

        # MOI E-Services: the model used to get STUCK on the catalog landing page. Drive the
        # start of the Tawtheeq sign-in DETERMINISTICALLY — click the navbar (English → Login →
        # Tawtheeq) ourselves, then request credentials. The OTP + navigation are then handled by
        # the model loop (with the playbook + human gates).
        if task.get("login_clicks") and not self._credentials:
            yield from self._eservices_login_start(task)
            return

        # Other workflows (e.g. PHCC login flows): inject the authoritative playbook and let
        # the model continue, anchored to the goal.
        playbook = workflows.build_playbook(task, self.workflow_values)
        self.messages.append({"role": "user", "content":
            playbook + "\n\nThe page is open and I have already auto-filled the known fields where found. "
                       "Continue with ONE JSON step for the REMAINING steps only (e.g. sign in, solve the "
                       "captcha via pause_for_user, submit, then read the result). STAY ON THIS TASK — do not "
                       "switch topics or answer unrelated questions. Use see_page to get numbered boxes before any click."})

    def _eservices_login_start(self, task: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Deterministically click the navbar to begin the Tawtheeq sign-in (English → Login →
        Tawtheeq), then request credentials (using a saved login if we have one). After that, the
        model loop drives OTP + the in-portal navigation, guided by the playbook."""
        # Inject the authoritative playbook so the loop has full context after sign-in.
        playbook = workflows.build_playbook(task, self.workflow_values)
        self.messages.append({"role": "user", "content":
            playbook + "\n\nI am starting the Tawtheeq sign-in for you now. After the username/password "
                       "and the OTP, continue with the NAVIGATE and Steps in order. STAY ON THIS TASK."})

        state: dict[str, Any] = {}
        for label in task.get("login_clicks", []):
            yield {"type": "action", "tool": "click", "input": {"target": label}}
            try:
                state = self._dispatch("click", {"target": label})
            except Exception as exc:  # noqa: BLE001
                state = {"error": str(exc)}
            self._tools_used += 1
            self.messages.append({"role": "assistant", "content": json.dumps(
                {"thought": f"open '{label}'", "action": "click", "action_input": {"target": label}}, ensure_ascii=False)})
            if isinstance(state, dict) and state.get("screenshot"):
                yield {"type": "screenshot", "url": state["screenshot"],
                       "page_url": state.get("url", ""), "title": state.get("title", "")}
            yield {"type": "observation", "tool": "click", "result": _trim(state)}
            self.messages.append({"role": "user", "content":
                f"Clicked the '{label}' navbar item. {_verify_note('click', state)}".strip()})

        # The Tawtheeq username/password form should now be showing → use a saved login or ask.
        login_url = (state.get("url") if isinstance(state, dict) else "") or task.get("landing", "")
        self._login_url = login_url
        if not self._use_saved_login(login_url):
            self._login_requested = True
            self.awaiting = True
            yield {"type": "awaiting_user",
                   "reason": "Sign in with Tawtheeq — enter your username and password and I'll log in. "
                             "You'll then get a one-time code (OTP) on your phone to confirm.",
                   "kind": "credentials", "fields": ["username", "password"]}

    def _autofill_workflow_inputs(self, elements: list[dict[str, Any]] | None) -> Iterator[dict[str, Any]]:
        """Fill each known workflow input deterministically. Text fields are matched
        semantically (English + Arabic) and filled by box; DATE fields use the universal
        smart date filler (handles a picker, a single field, OR 3 year/month/day boxes)."""
        if not elements:
            return
        used: set[int] = set()
        for spec in workflows.required_inputs(self.workflow or {}):
            value = self.workflow_values.get(spec["key"])
            if not value:
                continue
            syns = [s.lower() for s in spec.get("match", []) if s]

            # DATE field → value-based smart fill (finds the field itself, any shape).
            if spec.get("type") == "date":
                yield {"type": "action", "tool": "fill_date_smart", "input": {"value": value}}
                try:
                    res = self._dispatch("fill_date_smart", {"value": value, "synonyms": spec.get("match", [])})
                except Exception as exc:  # noqa: BLE001
                    res = {"error": str(exc)}
                self._tools_used += 1
                self.messages.append({"role": "assistant", "content": json.dumps(
                    {"thought": f"auto-fill {spec['label']}", "action": "fill_date_smart",
                     "action_input": {"value": value}}, ensure_ascii=False)})
                trimmed = _trim(res)
                if isinstance(res, dict) and res.get("screenshot"):
                    yield {"type": "screenshot", "url": res["screenshot"],
                           "page_url": res.get("url", ""), "title": res.get("title", "")}
                yield {"type": "observation", "tool": "fill_date_smart", "result": trimmed}
                mode = res.get("date_mode") if isinstance(res, dict) else None
                self.messages.append({"role": "user", "content":
                    f"Auto-filled {spec['label']} (date, mode={mode})."})
                continue

            # TEXT field → FIRST try the viewport-independent smart fill (finds the field by
            # name/id/placeholder/label anywhere on the page and scrolls to it). This fixes cases
            # where the field is below the fold or its <label> text didn't match a numbered box
            # (e.g. the QID box on the MOI traffic-violations form wasn't getting filled).
            smart_ok = False
            yield {"type": "action", "tool": "fill_text_smart", "input": {"value": value}}
            try:
                sres = self._dispatch("fill_text_smart", {"value": value, "synonyms": spec.get("match", [])})
                smart_ok = isinstance(sres, dict) and sres.get("text_filled")
            except Exception as exc:  # noqa: BLE001
                sres = {"error": str(exc)}
            self._tools_used += 1
            self.messages.append({"role": "assistant", "content": json.dumps(
                {"thought": f"auto-fill {spec['label']}", "action": "fill_text_smart",
                 "action_input": {"value": "***"}}, ensure_ascii=False)})
            if isinstance(sres, dict) and sres.get("screenshot"):
                yield {"type": "screenshot", "url": sres["screenshot"],
                       "page_url": sres.get("url", ""), "title": sres.get("title", "")}
            yield {"type": "observation", "tool": "fill_text_smart", "result": _trim(sres)}
            if smart_ok:
                self.messages.append({"role": "user", "content": f"Auto-filled {spec['label']}."})
                continue

            # Fallback → match a numbered box by synonyms, fill it.
            match_n = None
            for el in elements:
                n = el.get("n")
                if n in used or (el.get("tag") or "").lower() not in ("input", "textarea"):
                    continue
                typ = (el.get("type") or "").lower()
                if typ in ("button", "submit", "checkbox", "radio", "hidden"):
                    continue
                label = (el.get("label") or "").lower()
                if label and any(s in label for s in syns):
                    match_n = n
                    break
            if match_n is None:
                continue  # not found deterministically → the model will handle it via the playbook
            used.add(match_n)
            yield {"type": "action", "tool": "fill_mark", "input": {"n": match_n, "text": value}}
            try:
                res = self._dispatch("fill_mark", {"n": match_n, "text": value})
            except Exception as exc:  # noqa: BLE001
                res = {"error": str(exc)}
            self._tools_used += 1
            self.messages.append({"role": "assistant", "content": json.dumps(
                {"thought": f"auto-fill {spec['label']}", "action": "fill_mark",
                 "action_input": {"n": match_n, "text": value}}, ensure_ascii=False)})
            trimmed = _trim(res)
            if isinstance(res, dict) and res.get("screenshot"):
                yield {"type": "screenshot", "url": res["screenshot"],
                       "page_url": res.get("url", ""), "title": res.get("title", "")}
            yield {"type": "observation", "tool": "fill_mark", "result": trimmed}
            self.messages.append({"role": "user", "content":
                f"Auto-filled {spec['label']} into box #{match_n}. Observation: {json.dumps(trimmed, ensure_ascii=False)}"})

    def _fill_otp_and_continue(self) -> Iterator[dict[str, Any]]:
        """Type the user's one-time code into the OTP field and press Continue inside the OTP form
        (deterministic — the model used to click Continue without filling, or fill the wrong box).
        Then hand back to the model loop to finish the NAVIGATE steps."""
        wf = self.workflow or {}
        otp_field = wf.get("otp_field_selector", "#otp-field")
        otp_form = wf.get("otp_form_selector", "#mfaOtpFrm")
        code = self._otp_code
        self._otp_code = ""
        yield {"type": "thought", "content": "Entering the one-time code and continuing."}
        yield {"type": "action", "tool": "fill_otp", "input": {"field": otp_field, "form": otp_form}}
        try:
            res = self._dispatch("fill_otp", {"code": code, "field_selector": otp_field,
                                              "form_selector": otp_form})
        except Exception as exc:  # noqa: BLE001
            res = {"error": str(exc)}
        self._tools_used += 1
        # Record the action WITHOUT the code (never expose the OTP to the model/transcript).
        self.messages.append({"role": "assistant", "content": json.dumps(
            {"thought": "enter the one-time code", "action": "fill_otp",
             "action_input": {"field": otp_field}}, ensure_ascii=False)})
        if isinstance(res, dict) and res.get("screenshot"):
            yield {"type": "screenshot", "url": res["screenshot"],
                   "page_url": res.get("url", ""), "title": "One-time code entered"}
        trimmed = _trim(res)
        yield {"type": "observation", "tool": "fill_otp", "result": trimmed}
        self._post_login_url = res.get("url", "") if isinstance(res, dict) else ""
        obj = (f" [TASK: {self.workflow['name']} — stay on this task.]" if self.workflow else "")
        if isinstance(res, dict) and not res.get("otp_filled"):
            # Couldn't locate the OTP field — let the model recover via the page.
            self.messages.append({"role": "user", "content":
                f"Observation from fill_otp: {json.dumps(trimmed, ensure_ascii=False)}\n"
                f"I could not find the OTP field to fill. Use see_page, locate the one-time-code input, "
                f"fill it with the code the user provided earlier, and press Continue.{obj}"})
            return

        # After the OTP, a sign-in modal usually opens with an option preselected and a Login button
        # in its footer. Click that button DETERMINISTICALLY (ignore the options), so we land on the
        # E-services Catalog before handing back to the model — the model used to fumble this modal.
        modal_labels = wf.get("post_login_modal") or []
        if modal_labels:
            yield {"type": "thought", "content": "Confirming the sign-in dialog."}
            yield {"type": "action", "tool": "click_modal", "input": {"target": modal_labels[0]}}
            try:
                mres = self._dispatch("click_modal", {
                    "labels": list(modal_labels) + ["Continue", "Proceed", "Confirm"],
                    "timeout_ms": 8000})
            except Exception as exc:  # noqa: BLE001
                mres = {"error": str(exc)}
            self._tools_used += 1
            self.messages.append({"role": "assistant", "content": json.dumps(
                {"thought": "click Login in the sign-in modal", "action": "click_modal",
                 "action_input": {"target": modal_labels[0]}}, ensure_ascii=False)})
            if isinstance(mres, dict) and mres.get("screenshot"):
                yield {"type": "screenshot", "url": mres["screenshot"],
                       "page_url": mres.get("url", ""), "title": mres.get("title", "")}
            yield {"type": "observation", "tool": "click_modal", "result": _trim(mres)}
            if isinstance(mres, dict) and mres.get("url"):
                self._post_login_url = mres.get("url")

        nav = [s for s in (wf.get("navigation") or []) if s]
        nav_text = (" ".join(f"{i + 1}) {s}" for i, s in enumerate(nav)) if nav
                    else "Open the requested service from the E-services Catalog.")
        self.messages.append({"role": "user", "content":
            "You ARE now signed in to MOI E-Services — the one-time code was accepted and the sign-in "
            "dialog was confirmed. The E-services Catalog is on screen. Do NOT verify the login, do NOT "
            "use web_search, do NOT click Exit / Logout / Sign out, do NOT re-enter the username / "
            "password / OTP, and do NOT reopen the sign-in dialog or reload the page — you are logged "
            "in and must stay logged in. Proceed DIRECTLY with these navigation steps, clicking by the "
            f"visible label shown on the page: {nav_text} Use see_page to get numbered boxes before a "
            f"click if you need them.{obj}"})

    def _do_fill_login(self) -> Iterator[dict[str, Any]]:
        """Sign in deterministically with the user's stored credentials, then continue. Emits a
        frame showing the FILLED fields (so the user sees it happen) and self-corrects if the
        login page also needs a captcha (rare)."""
        scope = (self.workflow.get("login_form_selector", "") if self.workflow else "")
        # On bot-protected portals (MOI E-Services / Tawtheeq), type the credentials human-like
        # (real keystrokes + mouse) so an invisible reCAPTCHA doesn't reject the sign-in.
        humanize = bool(self.workflow and (self.workflow.get("human_login") or self.workflow.get("login_flow")))
        yield {"type": "thought", "content": "Signing you in securely with the details you provided."}
        yield {"type": "action", "tool": "fill_login", "input": {"submit": True}}
        try:
            res = self._dispatch("fill_login", {"submit": True, "scope": scope, "humanize": humanize})
        except Exception as exc:  # noqa: BLE001
            res = {"error": str(exc)}
        self._tools_used += 1
        self.messages.append({"role": "assistant", "content": json.dumps(
            {"thought": "log in", "action": "fill_login", "action_input": {"submit": True}}, ensure_ascii=False)})
        # Show the populated fields (pre-submit frame) then the resulting page.
        if isinstance(res, dict) and res.get("pre_submit_frame"):
            yield {"type": "screenshot", "url": res["pre_submit_frame"],
                   "page_url": res.get("url", ""), "title": "Credentials entered"}
        if isinstance(res, dict) and res.get("screenshot"):
            yield {"type": "screenshot", "url": res["screenshot"],
                   "page_url": res.get("url", ""), "title": res.get("title", "")}
        trimmed = _trim(res)
        yield {"type": "observation", "tool": "fill_login", "result": trimmed}

        # Login page also shows a captcha → fill was done, but we can't blind-submit. Ask the
        # human to solve it in the browser, then they press Continue and we carry on.
        if isinstance(res, dict) and res.get("needs_captcha") and not res.get("login_succeeded"):
            self.awaiting = True
            self.messages.append({"role": "user", "content":
                "[Paused] The sign-in page shows a verification code; the user solves it in the browser."})
            yield {"type": "awaiting_user", "kind": "captcha",
                   "reason": "Your details are entered. This sign-in also shows a verification code — please type "
                             "it in the browser window and press Sign in, then click Continue."}
            return

        # reCAPTCHA rejected the automated sign-in. How we hand off depends on the browser:
        #  • CDP (driving the user's OWN Chrome): finishing the sign-in in that window WILL pass —
        #    it's a real, reputable browser — so let the user complete it and resume.
        #  • Otherwise: this portal uses score-based reCAPTCHA Enterprise (an INVISIBLE check with
        #    no puzzle) that rejects automated browsers themselves — a human clicking in our window
        #    won't pass it. The honest fix is to switch to real-Chrome mode (start-chrome-debug.bat).
        if _recaptcha_failed(res) and not (isinstance(res, dict) and res.get("login_succeeded")):
            self.awaiting = True
            if self._browser_is_cdp():
                reason = ("The sign-in’s reCAPTCHA needs a real human. Switch to the Chrome window that "
                          "START.bat opened (your username & password are already filled there) and finish "
                          "signing in BY HAND — press Login, complete any check, and enter the OTP if it "
                          "asks — until you’re fully logged in. Then come back and click “I'm Done — "
                          "Continue” and I’ll carry on with the task. (If it keeps failing: make sure that "
                          "Chrome window is signed into a Google account and you’re not on a VPN.)")
                self.messages.append({"role": "user", "content":
                    "[Paused] reCAPTCHA on the sign-in; the user completes the whole sign-in (incl. OTP) by hand "
                    "in their own Chrome window, then we continue from the logged-in page."})
            else:
                reason = ("This portal’s sign-in is protected by reCAPTCHA Enterprise — an invisible bot check "
                          "that blocks automated browsers, so it only passes in a REAL Chrome (there’s no software "
                          "bypass). START.bat already opens that Chrome for you: 1) find the Chrome window titled "
                          "Google that opened, 2) sign into a Google account in it (one time — it stays signed in), "
                          "3) restart the backend and ask me again — I’ll drive that Chrome and the sign-in will go "
                          "through. If no such window is open, run start-chrome-debug.bat in the project folder. "
                          "Then click “I'm Done — Continue” to dismiss this.")
                self.messages.append({"role": "user", "content":
                    "[Paused] reCAPTCHA Enterprise blocked the automated sign-in; advised the user to sign into "
                    "Google in the real Chrome (CDP) that START.bat / start-chrome-debug.bat opens."})
            yield {"type": "awaiting_user", "kind": "manual_login", "reason": reason}
            return

        note = _verify_note("fill_login", res)
        self._post_login_url = res.get("url", "") if isinstance(res, dict) else ""
        obj = (f" [TASK: {self.workflow['name']} — stay on this task.]" if self.workflow else "")
        # Multi-step (Tawtheeq) logins have an OTP after the username/password → tell the model to
        # handle the OTP and then the navigation, not to "read data" yet.
        if self.workflow and self.workflow.get("login_flow"):
            filled = res.get("filled_fields", []) if isinstance(res, dict) else []
            if "password" not in filled:
                # The fill didn't take — do NOT advance to OTP. Let the model retry the sign-in.
                self.messages.append({"role": "user", "content":
                    f"Observation from fill_login: {json.dumps(trimmed, ensure_ascii=False)}.{note}\n"
                    f"The username/password fields were NOT filled (the Tawtheeq form may not be open yet). "
                    f"Use see_page to inspect the sign-in form (it is a section with id 'login-method'), make "
                    f"sure the Tawtheeq option is selected so the username & password fields are visible, then "
                    f"call fill_login again. Do NOT proceed to the OTP until the credentials are submitted.{obj}"})
            else:
                # Credentials submitted. The portal now sends an OTP. Handle it DETERMINISTICALLY:
                # wait for the OTP page, then pause for the user to type the code IN-APP — on resume
                # we fill #otp-field and click Continue inside #mfaOtpFrm ourselves (the model used
                # to press Continue without filling, or fill the wrong field).
                otp_field = self.workflow.get("otp_field_selector", "#otp-field")
                otp_form = self.workflow.get("otp_form_selector", "#mfaOtpFrm")
                present = False
                try:
                    det = self._dispatch("detect_otp", {"field_selector": otp_field,
                                                         "form_selector": otp_form, "timeout_ms": 7000})
                    present = bool(isinstance(det, dict) and det.get("otp_present"))
                except Exception:  # noqa: BLE001
                    present = False
                if present:
                    self._wf_phase = "otp_wait"
                    self.awaiting = True
                    yield {"type": "awaiting_user", "kind": "otp",
                           "reason": "A one-time code (OTP) was sent to your phone. Enter it here and I'll type "
                                     "it into the form and press Continue for you."}
                    return
                # OTP page not detected (slow render / different flow) → let the model continue.
                self.messages.append({"role": "user", "content":
                    f"Observation from fill_login: {json.dumps(trimmed, ensure_ascii=False)}.{note}\n"
                    f"I entered the username/password and submitted. If a one-time code (OTP) screen is now "
                    f"shown, use pause_for_user so the user enters it; then continue with the NAVIGATE steps "
                    f"to reach the service. Do NOT re-enter the username/password.{obj}"})
        else:
            self.messages.append({"role": "user", "content":
                f"Observation from fill_login: {json.dumps(trimmed, ensure_ascii=False)}.{note}\n"
                f"You are now signed in. Continue and READ the requested data — do not log in again.{obj}"})

    def _after_login(self) -> Iterator[dict[str, Any]]:
        """Drive a signed-in PHCC task deterministically: resolve the patient's record URL,
        navigate to it, then either read the result (direct_read), click through the named
        section and read it (section_path), or hand a non-deterministic task to the LLM loop."""
        task = self.workflow or {}
        cur_url = getattr(self, "_post_login_url", "") or ""
        # Capture the patientID from the post-login URL and navigate to the real record page.
        target = workflows.resolve_url(task, self.workflow_values)
        if "{patientID}" in target:
            m = re.search(r"/person/([^/?#]+)", cur_url)
            pid = m.group(1) if m else ""
            if pid and "{" not in pid and "%7b" not in pid.lower():
                self.workflow_values["patientID"] = pid
                target = workflows.resolve_url(task, self.workflow_values)
        if target and "{" not in target and target.rstrip("/") not in (cur_url or "").rstrip("/"):
            yield {"type": "action", "tool": "open_page", "input": {"url": target}}
            try:
                r = self._dispatch("open_page", {"url": target})
            except Exception as exc:  # noqa: BLE001
                r = {"error": str(exc)}
            if isinstance(r, dict) and r.get("screenshot"):
                yield {"type": "screenshot", "url": r["screenshot"],
                       "page_url": r.get("url", ""), "title": r.get("title", "")}
            yield {"type": "observation", "tool": "open_page", "result": _trim(r)}

        # Route deterministically.
        if task.get("direct_read"):
            yield from self._read_and_extract()
            return
        if task.get("section_path"):
            yield from self._navigate_section_and_extract(task["section_path"])
            return
        # Non-deterministic (e.g. send a message): leave _wf_phase != done → the run() loop
        # continues with the playbook that was injected at the login gate.
        if "{patientID}" not in workflows.resolve_url(task, self.workflow_values):
            self.messages.append({"role": "user", "content":
                f"You are signed in and on {target}. Continue with the remaining steps for this task."})

    def _navigate_section_and_extract(self, section_path: list[str]) -> Iterator[dict[str, Any]]:
        """Click each label in the portal's sidebar path (e.g. 'Health Record' → 'Medications'),
        then read + extract the section's content — deterministic, so the model can't wander."""
        yield {"type": "thought", "content": f"Opening {' › '.join(section_path)}."}
        for label in section_path:
            yield {"type": "action", "tool": "click", "input": {"target": label}}
            try:
                r = self._dispatch("click", {"target": label})
            except Exception as exc:  # noqa: BLE001
                r = {"error": str(exc)}
            if isinstance(r, dict) and r.get("screenshot"):
                yield {"type": "screenshot", "url": r["screenshot"],
                       "page_url": r.get("url", ""), "title": r.get("title", "")}
            yield {"type": "observation", "tool": "click", "result": _trim(r)}
        # Reveal hidden per-row detail (e.g. the medications' Dose/Frequency/Route behind their
        # "Show more info" toggles) BEFORE reading, when the task asks for it.
        expand = (self.workflow or {}).get("expand_all")
        if expand:
            yield {"type": "action", "tool": "expand_all", "input": {"labels": expand}}
            try:
                r = self._dispatch("expand_all", {"labels": expand})
            except Exception as exc:  # noqa: BLE001
                r = {"error": str(exc)}
            if isinstance(r, dict) and r.get("screenshot"):
                yield {"type": "screenshot", "url": r["screenshot"],
                       "page_url": r.get("url", ""), "title": r.get("title", "")}
            yield {"type": "observation", "tool": "expand_all", "result": _trim(r)}
        # Read the section WITHOUT re-navigating (we'd lose the clicked-into view).
        yield from self._read_and_extract(renav=False)

    # ------------------------------------------------------------------ #
    # Deterministic checkout (after the REVIEW PAYMENT gate)
    # ------------------------------------------------------------------ #
    def _det_step(self, tool: str, args: dict[str, Any], ok_fn,
                  *, retries: int = 1, wait_ms: int = 900) -> Iterator[dict[str, Any]]:
        """Run ONE deterministic step, retrying a few times because portal/gateway pages can be
        slow to render after a redirect or submit. Yields trace events live and never raises; the
        final result is stored on self._pay_last so the caller can branch on it. Shared by the
        payment checkout and the National Address Update flows."""
        yield {"type": "action", "tool": tool, "input": args}
        res: dict[str, Any] = {}
        attempts = max(1, retries)
        for i in range(attempts):
            try:
                res = self._dispatch(tool, args)
            except Exception as exc:  # noqa: BLE001
                res = {"error": str(exc)}
            if isinstance(res, dict) and res.get("url"):
                self._last_page_url = res["url"]
            try:
                if ok_fn(res):
                    break
            except Exception:  # noqa: BLE001
                pass
            if i < attempts - 1:
                time.sleep(wait_ms / 1000.0)
        self._pay_last = res if isinstance(res, dict) else {}
        if isinstance(res, dict) and res.get("screenshot"):
            yield {"type": "screenshot", "url": res["screenshot"],
                   "page_url": res.get("url", ""), "title": res.get("title", "")}
        yield {"type": "observation", "tool": tool, "result": _trim(res)}

    def _run_payment(self) -> Iterator[dict[str, Any]]:
        """Drive the checkout DETERMINISTICALLY after the user approved the REVIEW PAYMENT gate:
            review page "Pay"  →  Payment Method dialog (tick saved card type + Pay)  →
            payment-selection page ("NAPS" → "Proceed to Payment")  →  gateway card form
            (fill the saved card)  →  CONFIRM GATE before "Continue".
        This replaces the model loop, which used to skip the review-page Pay (so the Payment
        Method dialog never opened), hallucinate tools, and click the gateway buttons before the
        redirect had happened."""
        self._wf_phase = ""        # consume the phase so we don't re-enter on the next tick
        no_err = lambda r: not (isinstance(r, dict) and r.get("error"))

        yield {"type": "thought",
               "content": "Completing the payment: confirming the method, then filling the card."}

        # 1) Click "Pay" on the REVIEW page → opens the "Payment Method" dialog.
        yield from self._det_step("click", {"target": "Pay"}, no_err, retries=2, wait_ms=900)

        # 2) "Payment Method" dialog: tick the card-option radio (#qPayCardOptionRadio) + press Pay,
        #    which redirects to the QPAY gateway. Do it ONCE, then POLL for the gateway — and only
        #    re-run confirm_payment_method while we're STILL on the MOI dialog (so we don't re-fire it
        #    on the QPAY page and click the wrong thing). NAPS is only touched once QPAY is reached.
        gateway_markers = ("proceed to payment", "payment gateway", "debit and prepaid",
                           "select a payment method to continue", "himyan", "qpay")
        moi_modal_markers = ("total fee amount", "online bank", "redirected to the online")
        yield from self._det_step(
            "confirm_payment_method", {},
            lambda r: isinstance(r, dict) and r.get("pay_clicked"),
            retries=2, wait_ms=1000)
        reached_gateway = False
        for _ in range(6):
            time.sleep(1.2)   # let the redirect to the bank gateway happen
            text, _ = self._read_payment_page()
            low = text.lower()
            if any(k in low for k in gateway_markers):
                reached_gateway = True
                break
            if any(k in low for k in moi_modal_markers):   # still on the MOI dialog → Pay didn't take
                yield from self._det_step(
                    "confirm_payment_method", {},
                    lambda r: isinstance(r, dict) and r.get("pay_clicked"),
                    retries=1, wait_ms=800)
        if not reached_gateway:
            yield {"type": "thought", "content":
                   "The Payment Method dialog didn't redirect yet — trying the gateway anyway."}

        # 3) Payment-selection (QPAY) page: click "NAPS" (selects it), then "Proceed to Payment"
        #    (which is disabled until a card network is selected, so it may need a couple of tries).
        yield from self._det_step("click", {"target": "NAPS"}, no_err, retries=4, wait_ms=1300)
        yield from self._det_step("click", {"target": "Proceed to Payment"}, no_err,
                                  retries=4, wait_ms=1300)

        # 4) Bank gateway card form: fill the saved card (injected by code; never typed by the model).
        #    Success = something actually got filled (the form can lag behind the "Proceed" redirect,
        #    and fill returns no error when it finds no fields — so retry until card_filled is non-empty).
        yield from self._det_step(
            "fill_payment_card", {},
            lambda r: isinstance(r, dict) and (r.get("card_filled") or "no saved" in str(r.get("error", "")).lower()),
            retries=4, wait_ms=1300)
        if "no saved payment card" in str(self._pay_last.get("error", "")).lower():
            self.awaiting = False
            self._wf_phase = "done"
            self.workflow = None
            msg = ("I got as far as the secure card form, but there's no saved payment card to fill "
                   "it with. Add your card in the Payment panel (stored locally — never sent to me), "
                   "then ask me to pay again and I'll complete it.")
            yield {"type": "delta", "content": msg}
            yield {"type": "final", "content": msg}
            return

        # 5) CONFIRM GATE — pause for the user to confirm BEFORE pressing "Continue".
        self._wf_phase = "pay_finish"
        self.awaiting = True
        self.messages.append({"role": "user", "content":
            "[Paused] The card details are filled on the gateway. Waiting for the user to confirm "
            "the payment before I press Continue."})
        yield {"type": "awaiting_user", "kind": "confirm",
               "reason": "Your card details are filled in on the secure payment page. Confirm and "
                         "I'll press “Continue” to complete the payment. (If anything looks "
                         "off, you can finish it directly in the browser window instead.)"}

    def _finish_payment(self) -> Iterator[dict[str, Any]]:
        """The user confirmed → press "Continue" to complete the payment. If the bank then shows a
        card-verification step (3-D Secure / card OTP), pause for the user to finish it; otherwise
        report the result straight away."""
        self._wf_phase = "done"
        no_err = lambda r: not (isinstance(r, dict) and r.get("error"))

        yield {"type": "thought", "content": "Confirmed — pressing Continue to complete the payment."}
        yield from self._det_step("click", {"target": "Continue"}, no_err, retries=2, wait_ms=1200)

        text, page = self._read_payment_page()
        if isinstance(page, dict) and page.get("screenshot"):
            yield {"type": "screenshot", "url": page["screenshot"],
                   "page_url": page.get("url", ""), "title": page.get("title", "")}

        # A 3-D Secure / card-OTP screen may appear after Continue → let the user finish it in the
        # window, then (pay_report) we read where we landed and report success/failure.
        low = text.lower()
        if (any(k in low for k in ("one-time", "one time", "otp", "verification code", "secure code",
                                   "3-d secure", "3d secure", "authenticate your", "authentication code"))
                and not any(k in low for k in ("success", "completed", "thank you", "receipt"))):
            self._wf_phase = "pay_report"
            self.awaiting = True
            self.messages.append({"role": "user", "content":
                "[Paused] A card-verification (3-D Secure / OTP) screen appeared; the user completes "
                "it in the browser window."})
            yield {"type": "awaiting_user", "kind": "confirm",
                   "reason": "Your bank is asking for a one-time code to authorise the payment. "
                             "Please complete that in the browser window, then click Continue here "
                             "and I'll confirm the result."}
            return

        yield from self._report_payment_success(text)

    def _report_payment(self) -> Iterator[dict[str, Any]]:
        """After the user finished a 3-D Secure / card-OTP step: read where we landed and report."""
        self._wf_phase = "done"
        text, page = self._read_payment_page()
        if isinstance(page, dict) and page.get("screenshot"):
            yield {"type": "screenshot", "url": page["screenshot"],
                   "page_url": page.get("url", ""), "title": page.get("title", "")}
        yield from self._report_payment_success(text)

    def _read_payment_page(self) -> tuple[str, dict[str, Any]]:
        """Read the current page text (grounding for the success report). Never raises."""
        try:
            page = self._dispatch("read_page", {})
        except Exception:  # noqa: BLE001
            page = {}
        text = (page.get("text", "") if isinstance(page, dict) else "") or ""
        return text, (page if isinstance(page, dict) else {})

    def _report_payment_success(self, page_text: str) -> Iterator[dict[str, Any]]:
        """Stream a payment-completion message GROUNDED in the result page (so we never claim a
        success the page doesn't show). Mentions emailed delivery for certificate-style services."""
        wf = self.workflow or {}
        emailed = bool(wf.get("inputs") and "email" in wf.get("inputs", []))
        email = self.workflow_values.get("email", "")
        msgs = [
            {"role": "system", "content":
             "You are Fanar. The user just completed a payment on a Qatar government e-service. Read "
             "the result page text and tell them, in one or two short sentences, whether the payment "
             "and request went through. Use ONLY what the page shows (look for confirmations like "
             "'Transaction completed successfully', a reference/receipt number, or a clear error). If "
             "the page clearly confirms success, congratulate them briefly and include any reference "
             "number shown. If it does NOT clearly confirm success, say honestly that you pressed "
             "Continue but couldn't confirm completion, and suggest they check the browser window. "
             "Never invent a reference number or a success the page doesn't state. You may use simple "
             "Markdown (e.g. **bold** for the reference number)." + self._lang_instr()},
            {"role": "user", "content":
             f"Service: {wf.get('service_name', '')} — {wf.get('name', '')}\n"
             f"{('The certificate/document is delivered by email' + (f' to {email}' if email else '') + '.') if emailed else ''}\n\n"
             f"Result page text:\n{(page_text or '')[:5000]}"},
        ]
        acc = ""
        try:
            for chunk in self.client.chat_stream(msgs, model=self.client.default_model,
                                                 temperature=0.2, max_tokens=300):
                acc += chunk
                yield {"type": "delta", "content": chunk}
        except FanarError:
            acc = ""
        if not acc.strip():
            acc = self._t("I pressed Continue to complete the payment. Please check the browser window to "
                          "confirm it went through" + (f"; your certificate will be emailed to {email}." if email and emailed else "."))
            yield {"type": "delta", "content": acc}
        yield {"type": "final", "content": acc}

    # ------------------------------------------------------------------ #
    # Deterministic Replace Lost/Damaged ID Card form
    # ------------------------------------------------------------------ #
    def _infer_service_type(self) -> str:
        """Work out 'Replace Lost' vs 'Replace Damaged' from the user's request — using BOTH the
        English translation and the raw Arabic (so "لقد فقدتُ بطاقتي" = lost is detected whether or
        not the translation happens to say "lost")."""
        return _classify_lost_damaged(self._last_user_en, self._last_user)

    def _fill_id_card_and_review(self) -> Iterator[dict[str, Any]]:
        """The Replace Lost/Damaged ID Card form is on screen → fill it DETERMINISTICALLY (Service
        Type radio by id, "My QID" radio by id, Next, then the OK delivery dialog), then read the
        payment review and pause for the user to approve the fee (which arms the payment flow)."""
        stype = self._id_card_service or self._infer_service_type()
        if not stype:
            # Couldn't tell lost vs damaged → ask, then resume into the fill (id_card_fill phase).
            self._wf_phase = "id_card_ask"
            self.awaiting = True
            yield {"type": "awaiting_user", "kind": "info",
                   "reason": "Do you want to replace a LOST or a DAMAGED ID card?",
                   "fields": [{"key": "service_type", "label": "Lost or Damaged", "type": "text",
                               "placeholder": "lost or damaged"}]}
            return
        self._id_card_done = True
        yield {"type": "thought", "content": f"Filling the Replace ID Card form ({stype})."}
        yield from self._det_step(
            "fill_id_card_form", {"service_type": stype},
            lambda r: isinstance(r, dict) and r.get("service_set") and r.get("qid_set"),
            retries=3, wait_ms=900)
        yield from self._review_payment_gate()

    def _structure_review(self, det: dict[str, Any]) -> dict[str, Any]:
        """Make sure the REVIEW PAYMENT details are correctly paired before showing them.

        PRIMARY path: the extractor (`_PAYMENT_REVIEW_JS`) already pairs each labelled <input>/<select>
        with its OWN label (the MOI page is a Bootstrap form), so when `det['address']` has real
        values we TRUST it and skip the model entirely (fast, exact, no PII to the model).

        FALLBACK path (a plain-text review page with NO input fields): the visible text is grouped
        (all labels, then all values), which no same-row heuristic can pair — so we hand the ordered
        `det['lines']` to the model to pair by order + meaning. Fails open to whatever heuristic rows
        exist. Only the user's own review data (fee, address, phone) is ever sent — never credentials,
        card numbers, or OTP codes."""
        if not isinstance(det, dict):
            return det
        addr = det.get("address") or []
        have_pairs = any(isinstance(r, dict) and str(r.get("value", "")).strip() for r in addr)
        if have_pairs:
            det.pop("lines", None)   # deterministic pairs are reliable → don't bother the model
            return det
        lines = [str(x) for x in (det.get("lines") or []) if str(x).strip()]
        if not lines:
            return det
        system = (
            "You extract fields from a Qatar government e-service PAYMENT REVIEW page. You are given "
            "the page's visible text IN ORDER, one item per line — these are field LABELS and their "
            "VALUES. The page may list several labels first and then their values, so pair each "
            "label with its CORRECT value using BOTH order and meaning. Guidance: a Mobile/Phone "
            "number has ~7 or more digits; Zone/Street/Building/Unit/Floor/Apartment are usually "
            "small numbers; an Email contains '@'. Do NOT invent values; if a label has no matching "
            "value, omit it. Ignore button captions (Pay, Continue, Cancel, OK) and section "
            "headings. Reply with ONLY a JSON object of this exact shape: "
            '{"total_fees": "<fee with currency if shown, else empty>", '
            '"fields": [{"label": "<field name>", "value": "<value>"}]}. '
            "Keep labels and values exactly as written (English text and digits)."
        )
        user = ("Fee hint: " + (det.get("total_fees") or "(none)") + "\n\n"
                "Visible text (in order):\n" + "\n".join(lines[:90]))
        try:
            raw = self.client.chat(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                model=self.client.default_model, temperature=0.0, max_tokens=700)
        except FanarError:
            return det
        obj = _extract_json(raw or "")
        if not isinstance(obj, dict):
            return det
        rows: list[dict[str, str]] = []
        seen: set[str] = set()
        for f in (obj.get("fields") or []):
            if not isinstance(f, dict):
                continue
            lab = str(f.get("label", "")).strip().rstrip(":*").strip()
            val = str(f.get("value", "")).strip()
            key = lab.lower()
            if lab and val and key not in seen and len(lab) <= 40 and len(val) <= 80:
                seen.add(key)
                rows.append({"label": lab, "value": val})
        if rows:
            det["address"] = rows
        fee = str(obj.get("total_fees", "")).strip()
        if fee:
            det["total_fees"] = fee
        det.pop("lines", None)   # internal only — don't ship the raw line dump to the UI
        return det

    def _review_payment_gate(self) -> Iterator[dict[str, Any]]:
        """Read the REVIEW PAYMENT page (service fee + details) and pause for the user to approve —
        arming the deterministic checkout (review_gate → pay → _run_payment) on resume."""
        res: dict[str, Any] = {}
        last_good: dict[str, Any] = {}
        # The disabled review inputs populate via AJAX after the delivery "OK" dialog, so POLL until
        # the page actually shows VALUES (a fee or a filled row) — don't settle for labels-only
        # (`raw` is non-empty from labels alone, which used to make us read an empty page and show
        # the user a blank review). Keep the best non-empty read as a fallback.
        for _ in range(8):
            time.sleep(0.8)
            try:
                res = self._dispatch("review_payment", {})
            except Exception:  # noqa: BLE001
                res = {}
            det0 = res.get("details", {}) if isinstance(res, dict) else {}
            has_values = bool(det0.get("total_fees")) or any(
                isinstance(r, dict) and str(r.get("value", "")).strip() for r in (det0.get("address") or []))
            if has_values:
                last_good = det0
                break
            if det0.get("raw") and not last_good:
                last_good = det0   # remember the labels-only read in case values never load
        det = dict(last_good or (res.get("details", {}) if isinstance(res, dict) else {}))
        det = self._structure_review(det)   # pair labels↔values correctly via the model
        if not det.get("email"):
            det["email"] = self.workflow_values.get("email", "")
            if not det["email"]:
                try:
                    det["email"] = profile_store.load_profile().get("email", "")
                except Exception:  # noqa: BLE001
                    det["email"] = ""
        self._wf_phase = "review_gate"
        self.awaiting = True
        self.messages.append({"role": "user", "content": "[Paused] Showing the payment review for approval."})
        yield {"type": "awaiting_user", "kind": "review",
               "reason": (res.get("reason") if isinstance(res, dict) else "")
                         or "Review the payment details before proceeding.",
               "details": det}

    # ------------------------------------------------------------------ #
    # Deterministic National Address Update (after the first "Update")
    # ------------------------------------------------------------------ #
    def _finish_address_update(self) -> Iterator[dict[str, Any]]:
        """The user reviewed/changed the editable address fields → write them, then finish the
        Update DETERMINISTICALLY: fill → "Next" → "Update" → acknowledgement dialog "Continue" →
        read the page, verify "Transaction completed successfully", and report. (The model used to
        wander after the first Update — clicking the wrong things or never confirming.)"""
        self._wf_phase = "done"
        no_err = lambda r: not (isinstance(r, dict) and r.get("error"))

        yield {"type": "thought", "content": "Saving your National Address update."}

        # 1) Write the reviewed values into the editable form (the fields were tagged at read time,
        #    so this fills exactly what the user saw). Success = something was actually filled, so it
        #    retries if the form is still settling. No-op if the user changed nothing.
        if self._edit_values:
            # Success = NOTHING left unfilled. Retries with waits matter for the Zone/Street/Building
            # dropdowns: they cascade (selecting Zone loads Street's options via AJAX, etc.), so a
            # field "missed" on one pass becomes fillable on the next. Each pass is idempotent
            # (selects already on the right option are skipped) so re-passing won't reset them.
            yield from self._det_step(
                "fill_editable_form", {"values": self._edit_values},
                lambda r: isinstance(r, dict) and not r.get("missed"),
                retries=5, wait_ms=800)
        self._edit_values = {}

        # 2) Click "Next" → a review screen → click "Update" → the endorsement/acknowledgement
        #    dialog appears → click "Continue".
        yield from self._det_step("click", {"target": "Next"}, no_err, retries=2, wait_ms=1100)
        yield from self._det_step("click", {"target": "Update"}, no_err, retries=2, wait_ms=1100)
        yield from self._det_step(
            "click_modal", {"labels": ["Continue", "OK", "Confirm", "Submit"]},
            lambda r: isinstance(r, dict) and (r.get("modal_clicked") or r.get("modal_found")),
            retries=3, wait_ms=1100)

        # 3) Read the result page and report success grounded in it.
        text, page = self._read_payment_page()
        if isinstance(page, dict) and page.get("screenshot"):
            yield {"type": "screenshot", "url": page["screenshot"],
                   "page_url": page.get("url", ""), "title": page.get("title", "")}
        yield from self._report_update_success(text)

    def _report_update_success(self, page_text: str) -> Iterator[dict[str, Any]]:
        """Stream a grounded success/failure message for the National Address Update."""
        wf = self.workflow or {}
        svc = wf.get("name", "") or "request"
        msgs = [
            {"role": "system", "content":
             "You are Fanar. The user just submitted a Qatar government e-service request. Read the "
             "result page text and tell them in one or two short sentences whether it went through. "
             "Decide success ONLY from what the page shows (a confirmation like 'Transaction completed "
             "successfully', a reference/receipt number, or a clear error). BUT phrase the message in "
             f"terms of WHAT THEY DID — '{svc}' — NOT the portal's literal words. For example, for an "
             "address update say something like \"Done — your National Address update is complete!\" "
             "(then add any reference number shown), NOT \"Your transaction was completed "
             "successfully.\" If the page does NOT clearly confirm success, say honestly that you "
             "submitted it but couldn't confirm, and suggest checking the browser window. Never invent "
             "a success the page doesn't state. You may use simple Markdown (**bold** for a reference "
             "number)." + self._lang_instr()},
            {"role": "user", "content":
             f"Service: {wf.get('service_name', '')} — {svc}\n\n"
             f"Result page text:\n{(page_text or '')[:5000]}"},
        ]
        acc = ""
        try:
            for chunk in self.client.chat_stream(msgs, model=self.client.default_model,
                                                 temperature=0.2, max_tokens=250):
                acc += chunk
                yield {"type": "delta", "content": chunk}
        except FanarError:
            acc = ""
        if not acc.strip():
            acc = self._t("Done — I submitted your National Address update. Please check the browser "
                          "window to confirm it went through.")
            yield {"type": "delta", "content": acc}
        yield {"type": "final", "content": acc}

    def _submit_and_extract(self) -> Iterator[dict[str, Any]]:
        """After the user provided the verification code: enter it, press the inquiry form's
        Submit/Search button OURSELVES (scoped to the form holding the inputs — never the site
        search form), then read the result and answer. Deterministic so the model can't wander."""
        self._wf_phase = "done"

        # 1) Enter the verification code into the captcha box (re-locate fresh — marks can shift).
        if self._captcha_code:
            yield {"type": "thought", "content": "Entering the verification code and submitting the inquiry."}
            try:
                cap = self._dispatch("capture_captcha", {})
            except Exception:  # noqa: BLE001
                cap = {}
            in_mark = (cap.get("input_mark") if isinstance(cap, dict) else None) or self._captcha_input_mark
            if in_mark is not None:
                yield {"type": "action", "tool": "fill_mark", "input": {"n": in_mark, "text": "••••••"}}
                try:
                    self._dispatch("fill_mark", {"n": int(in_mark), "text": self._captcha_code})
                except Exception:  # noqa: BLE001
                    pass
            self._captcha_code = ""
        else:
            yield {"type": "thought", "content": "Submitting the inquiry and reading the result."}

        # Snapshot the page BEFORE submitting so we can tell whether the submit actually did
        # anything (the result often loads async on these Angular portals).
        pre = self._dispatch("read_page", {})
        pre_url = pre.get("url", "") if isinstance(pre, dict) else ""
        pre_len = len((pre.get("text", "") if isinstance(pre, dict) else "") or "")

        # 2) Press the Submit / Search button. Try the button INSIDE the input form first (never
        #    the site-wide searchQuery form), then ESCALATE — many MOI pages (e.g. the fees2
        #    "Pay Traffic Violations" SPA) don't submit on the first form-scoped click, so we fall
        #    back to a page-wide submit (which also presses Enter in the last field) and verify the
        #    page actually changed before giving up.
        yield {"type": "action", "tool": "submit_inquiry_form", "input": {}}
        try:
            r = self._dispatch("submit_inquiry_form", {})
        except Exception:  # noqa: BLE001
            r = {}
        page_text, result_state, changed = self._poll_result(pre_url, pre_len)

        if not changed:
            # Escalation: page-wide submit (covers SPA buttons outside a <form> + Enter-to-submit).
            yield {"type": "action", "tool": "submit_form", "input": {}}
            try:
                self._dispatch("submit_form", {})
            except Exception:  # noqa: BLE001
                pass
            page_text, result_state, changed = self._poll_result(pre_url, pre_len)

        # 3) Read the result and write a grounded answer (honest if nothing came back).
        if isinstance(result_state, dict) and result_state.get("screenshot"):
            yield {"type": "screenshot", "url": result_state["screenshot"],
                   "page_url": result_state.get("url", ""), "title": result_state.get("title", "")}
        yield {"type": "observation", "tool": "read_page", "result": _trim(result_state)}
        yield from self._extract_final(page_text, result_obtained=changed)

    def _poll_result(self, pre_url: str, pre_len: int, tries: int = 5) -> tuple[str, dict[str, Any], bool]:
        """Re-read the page a few times after submitting until the result settles — the page either
        changes from the pre-submit form (new URL / much more text) or shows a result keyword.
        Returns (page_text, last_state, changed). `changed` is False if it still looks like the
        unsubmitted form, so the caller can answer honestly instead of summarising the form."""
        hints = ("violation", "fine", "penalty", "no record", "no violation", "not eligible",
                 "eligible", "expiry", "result")
        text, state, changed = "", {}, False
        for i in range(tries):
            state = self._dispatch("read_page", {})   # read_page settles the page (waits for net idle)
            text = state.get("text", "") if isinstance(state, dict) else ""
            url = state.get("url", "") if isinstance(state, dict) else ""
            low = (text or "").lower()
            loading = any(s in low for s in ("loading", "please wait"))
            big_change = (url != pre_url) or (abs(len(text) - pre_len) > 250)
            if not loading and len(low) > 80 and (big_change or any(h in low for h in hints)):
                changed = True
                break
            if i < tries - 1:
                time.sleep(0.8)   # also catch results rendered by a timer/animation (no XHR to idle on)
        return text, state, changed

    def _read_and_extract(self, renav: bool = True) -> Iterator[dict[str, Any]]:
        """Deterministically read the (already-open) result page and write a grounded answer —
        for read-only inquiries whose page shows the result directly (e.g. appointments). No LLM
        loop, so the model can't wander/hallucinate. Re-reads a few times so SPA data can settle.
        renav=False keeps the current view (used after clicking into a section)."""
        self._wf_phase = "done"
        yield {"type": "thought", "content": "Reading your information directly from the page."}
        url = workflows.resolve_url(self.workflow or {}, self.workflow_values) if renav else ""
        page_text, result_state = "", {}
        for attempt in range(3):
            result_state = self._dispatch("read_page", {})
            page_text = result_state.get("text", "") if isinstance(result_state, dict) else ""
            low = (page_text or "").lower()
            settled = len(low) > 120 and not any(s in low for s in
                                                 ("loading", "please wait"))
            if settled or attempt == 2:
                break
            if renav and url and "{" not in url:   # re-load the page once and try again (idempotent GET)
                try:
                    self._dispatch("open_page", {"url": url})
                except Exception:  # noqa: BLE001
                    pass
            else:
                self._dispatch("read_page", {})     # brief no-op read to let the SPA settle
        if isinstance(result_state, dict) and result_state.get("screenshot"):
            yield {"type": "screenshot", "url": result_state["screenshot"],
                   "page_url": result_state.get("url", ""), "title": result_state.get("title", "")}
        yield {"type": "observation", "tool": "read_page", "result": _trim(result_state)}
        yield from self._extract_final(page_text)

    def _extract_final(self, page_text: str, result_obtained: bool = True) -> Iterator[dict[str, Any]]:
        """Write the final answer from the result page (focused context — no full transcript,
        so it stays exactly on task). `result_obtained=False` means the page still looks like the
        un-submitted input form, so we tell the model to be honest instead of summarising it (this
        is what stops the 'download Metrash / you need to provide…' generic hallucination)."""
        wf = self.workflow or {}
        guard = ("" if result_obtained else
                 "\n\nIMPORTANT: the inquiry may NOT have returned a result — the page below may still be the "
                 "INPUT FORM (it shows fields like ID number / security code / a Submit/Search button) rather "
                 "than violation/fine/result data. If you do not see ACTUAL result data (e.g. specific "
                 "violations, fines, amounts, dates, or an explicit 'no violations' message), do NOT summarise "
                 "the form and do NOT give general guidance. Instead say briefly that you filled in the details "
                 "and pressed Submit but the result did not load, and ask the user to press the Search "
                 "button in the browser window to complete the inquiry.")
        fmt = wf.get("answer_format", "")
        msgs = [
            {"role": "system", "content":
             "You are Fanar. Read the page text from a Qatar e-service (a government or health portal) and "
             "answer the user's request precisely and concisely. Use "
             "ONLY facts present in the page text — NEVER use outside knowledge or memory. NEVER suggest "
             "external apps (e.g. Metrash), websites, hotlines, offices, or how-to instructions. If the page "
             "shows nothing (e.g. 'No appointments scheduled', an empty list), report that clearly and simply. "
             "If the requested data isn't on the page (an error, sign-in needed, nothing returned, or only the "
             "input form), say so plainly and briefly — do not invent or generalise. Ignore page menus/"
             "navigation/chrome. Do NOT change the topic or add unrelated information. Use Markdown when it "
             "helps readability (e.g. a table or a bullet list)." + self._lang_instr()},
            {"role": "user", "content":
             f"User request: {self._last_user}\nService: {wf.get('service_name', '')} — {wf.get('name', '')}\n\n"
             f"Page text:\n{(page_text or '')[:8000]}\n\n"
             f"Report the relevant result clearly (e.g. appointments, document expiry dates, fines, eligibility). "
             f"If there are none, say so directly.{(' ' + fmt) if fmt else ''}{guard}"},
        ]
        acc = ""
        failed = False
        try:
            for chunk in self.client.chat_stream(msgs, model=self.client.default_model,
                                                 temperature=0.2, max_tokens=600):
                acc += chunk
                yield {"type": "delta", "content": chunk}
        except FanarError:
            acc = ""
            failed = True
        if not acc.strip():
            acc = (self._service_unavailable_msg() if failed else
                   self._t("I submitted the inquiry — please check the result shown in the browser window."))
            yield {"type": "delta", "content": acc}
        yield {"type": "final", "content": acc}

    def _compact(self) -> None:
        """Prune all but the two most recent observation messages to keep the context
        small (large context = slow inference). Older steps keep only a one-line stub."""
        obs_idx = [i for i, m in enumerate(self.messages)
                   if m["role"] == "user" and m["content"].startswith("Observation from")]
        for i in obs_idx[:-2]:
            head = self.messages[i]["content"].split(":", 1)[0]
            if len(self.messages[i]["content"]) > 220:
                self.messages[i] = {"role": "user", "content": f"{head}: …[older observation pruned]"}

    def _stream_smalltalk(self) -> Iterator[dict[str, Any]]:
        """Answer a greeting / small-talk directly (streamed), without any tools."""
        msgs = [
            {"role": "system", "content":
             "You are Fanar, the FANAR Government Navigator — a friendly assistant for Qatar. "
             "Reply briefly and warmly to this greeting/small-talk, then offer to help "
             "with government services, healthcare, or education. One or two short sentences. You may "
             "use light Markdown (e.g. **bold**)." + self._lang_instr()},
            {"role": "user", "content": self._last_user},
        ]
        acc = ""
        failed = False
        try:
            for chunk in self.client.chat_stream(msgs, model=self.client.default_model,
                                                 temperature=0.5, max_tokens=160):
                acc += chunk
                yield {"type": "delta", "content": chunk}
        except FanarError:
            acc = ""
            failed = True
        if not acc.strip():
            acc = (self._service_unavailable_msg() if failed else
                   self._t("Hello! I'm Fanar — I can help you with Qatar government services, healthcare, and education. What would you like to do?"))
            yield {"type": "delta", "content": acc}
        yield {"type": "final", "content": acc}

    def _stream_final(self, draft: str) -> Iterator[dict[str, Any]]:
        """Write the user-facing final answer with the QUALITY model, streamed token-by-token."""
        msgs = list(self.messages)
        msgs.append({"role": "user", "content":
            "Now write the FINAL answer to the user based on everything you gathered above. "
            "Be clear, helpful and well-structured, and format with Markdown where it helps readability "
            "(headings, **bold**, bullet or numbered lists, and tables for tabular data). "
            "No JSON, no tool calls." + self._lang_instr()})
        msgs = _fit(msgs, 8000)
        acc = ""
        failed = False
        try:
            for chunk in self.client.chat_stream(msgs, model=self.client.default_model,
                                                 temperature=0.3, max_tokens=700):
                acc += chunk
                yield {"type": "delta", "content": chunk}
        except FanarError:
            acc = ""
            failed = True
        if not acc.strip():
            # streaming failed — use the planner's draft answer, or a clear service-down message.
            acc = (draft.strip() if draft and draft.strip()
                   else (self._service_unavailable_msg() if failed else ""))
            if acc:
                yield {"type": "delta", "content": acc}
        yield {"type": "final", "content": acc}
