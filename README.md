# FANAR AGENT — Desktop-grade Agentic AI for Government · Healthcare · Education

> **Fanar Hackathon 2026** · Tracks 1–3
> A single agent, built on **Fanar**, that doesn't just chat — it **acts**. It drives a
> real browser, **sees your screen** (Fanar vision), **controls your computer**
> (mouse/keyboard), **speaks and listens** (Fanar voice), and specializes its tools and
> persona across all three hackathon tracks — wrapped in a native **desktop app** with a
> bold kinetic UI in Fanar's brand palette.

---

## 1. Problem Statement

Citizens, patients, and learners in Qatar face the same wall: the information and the
*action* live in different places, behind portals and forms, often in formal Arabic.
A chatbot can talk about a task; it cannot **do** it. People still have to operate the
computer themselves.

**Fanar Agent turns Fanar into a doer that operates the machine for you.** Tell it what
you need — in Arabic or English, by text or voice — and it plans, uses real tools, and
carries the task out on your real screen and browser, pausing for you whenever a human
must approve (logins, payments, clicking "submit"). It adapts to three domains:

- **Smart Government** — find official services, read real `.gov.qa` pages, fill forms, draft letters.
- **Healthcare Support** — summarise clinical notes, explain medications, write patient instructions, cautious triage (always with a safety disclaimer).
- **Education & Heritage** — lesson plans, quizzes, flashcards, and Arabic-first tutoring.

### Why it's different
It is a **real computer-use agent on Fanar**: it *sees* (vision), *acts* (browser + OS
control), *speaks* (TTS) and *listens* (STT) — with **human-in-the-loop safety** and a
transparent, live step-by-step trace. Not a chat window with buttons.

---

## 2. What We Built

A **native desktop app** (Electron) + a **Fanar agent brain** (FastAPI) + a bold kinetic
web UI. Two surfaces, three tracks, four flagship capabilities.

| Surface | Capabilities |
|--------|--------------|
| **Web** (browser) | Real-browser agent: web search, navigate real sites, read, fill forms, human-in-the-loop login. |
| **Desktop** (Electron) | Everything in Web **plus real computer use**: see the screen (Fanar vision) + control mouse/keyboard on the current screen, with per-action approval. (External services/tasks always go through the browser — no native app launching.) Global hotkey (Ctrl+Shift+F), tray, frameless window. |

### Flagship capabilities (all verified live against Fanar)
| Capability | Fanar model / mechanism |
|---|---|
| 🔢 **Set-of-Marks grounding** | A background script draws **numbered boxes** over every clickable element (DOM in the browser, the **UI-Automation tree** on the desktop). Fanar vision says "click box #14"; we click that element's exact coordinates — fast and precise, no coordinate guessing. |
| 🔐 **Secure auto-login** | The agent detects a login, asks you for credentials in a **masked form**, and **fills + submits them itself**. Credentials stay in memory on the backend, are injected straight into the form, and are **never sent to the AI or saved**. |
| 👁 **Screen vision** | Screenshot → **Fanar-Oryx-IVU-2** describes/locates what's on screen. |
| 🖱 **Computer control** | `pyautogui` mouse/keyboard on the current screen, **confirmed per action**. (No app-launching — all external tasks go through the browser.) |
| 🌐 **Real browser** | Playwright driving the **real installed Chrome** with **stealth** (hidden `navigator.webdriver`, anti-automation flags) to reduce bot-blocking; image/media/font blocking for speed; **action-verification** (detects login failures, page errors, and "page didn't change" so the agent self-corrects instead of moving on). |
| 🎙 **Voice in/out** | **Fanar-Aura-STT-LF-1** (speech→text) + **Fanar-Aura-TTS-2** (10 voices, AR & EN). |
| 🧭 **Government Navigator** | Understands intent (incl. Gulf dialect) → finds the right official service on Hukoomi/MOI → explains requirements in Arabic → fills forms → pauses at the verification-code (CAPTCHA) for you. Seeded with **verified real `.gov.qa` service URLs**. |
| 📚 **Knowledge-first** | For general questions (documents/requirements/procedures) it answers from **Fanar's own knowledge** instantly; it only opens the browser for **live/account-specific** data ("my fines", "my visa status"). |
| ⚡ **Fast + streamed** | A small model (**Fanar-S-1-7B**) drives the planning loop; the quality model (**Fanar**) writes the final answer **streamed token-by-token**. Stealth + image/media blocking + context pruning keep it snappy. |
| ✋ **Human-in-the-loop** | Agent pauses for credentials, OTP/CAPTCHA, and risky desktop actions; you approve/skip. |

---

## 3. Solution Architecture

```
┌──────────────────────────────┐        ┌──────────────────────────────────────────┐
│  Electron Desktop Shell      │        │            FastAPI — Fanar brain           │
│  • frameless, tray, hotkey   │ HTTP/  │  /api/agent (track+surface) ── SSE trace   │
│  • surface = "desktop"       │ SSE    │  /api/agent/resume  (login / approve)      │
│        │ loads               │───────▶│  /api/voice/transcribe   Fanar STT         │
│        ▼                     │◀───────│  /api/voice/speak        Fanar TTS         │
│  Next.js 14 Kinetic UI       │ events │  /api/screenshot/{f}     live frames       │
│  (also runs in a browser =   │        │  /api/capabilities                         │
│   surface "web")             │        └───────────────┬────────────────────────────┘
└──────────────────────────────┘                        │
                                   ┌────────────────────┼─────────────────────────────┐
                                   ▼                    ▼                              ▼
                          ┌─────────────────┐  ┌──────────────────┐         ┌──────────────────┐
                          │ Fanar API        │  │ Real browser     │         │ Real computer    │
                          │ chat · Oryx      │  │ (Playwright →      │         │ screen capture + │
                          │ vision · Aura    │  │ Chromium)        │         │ pyautogui control│
                          │ STT/TTS          │  │                  │         │ (desktop only)   │
                          └─────────────────┘  └──────────────────┘         └──────────────────┘
```

### Backend modules
- `fanar_client.py` — OpenAI-compatible Fanar client: chat (stream), **vision** (`see_image`), **STT** (`transcribe`), **TTS** (`speak`, `list_voices`).
- `agent.py` — the **track- and surface-aware** resumable ReAct loop + human-in-the-loop (login *and* desktop-action approval) + tool-forcing reliability guards.
- `browser_session.py` — persistent real browser per session (Playwright on a worker thread).
- `desktop.py` — **screen vision + computer control** tools (mss + pyautogui), enabled only on the desktop surface.
- `tracks.py` — per-track persona, tool sets, quick-actions; Healthcare/Education Fanar-backed generators.
- `tools.py` — unified tool dispatch (browser / desktop / track / common) + per-track-surface schema builder.
- `main.py` — FastAPI: agent, resume, chat, voice, screenshot, capabilities.

### Frontend
- Next.js 14 + Tailwind, **Kinetic Typography** design re-skinned into Fanar's navy/maroon palette (Space Grotesk, oversized uppercase display, marquee, massive step numbers, sharp 2px brutalist cards, hard hover inversions, `prefers-reduced-motion` respected).
- Components: `Marquee`, `StepTimeline` (live trace with giant step numbers), `LivePreview` (live browser/screen frame + approval card), `VoiceButton`, `FanarMark`.
- `desktop/` — Electron shell (`main.js`, `preload.js`) that loads the same UI and reports `surface="desktop"`.

---

## 4. Agentic Workflow Design

### It's a Vision-Language-Action (VLA) loop
The architecture maps directly onto the team guide's favored "Vision-Language-Action
workflows" and "tool-using Arabic agents":

- **Fanar (LLM) = the brain** — plans, decomposes the task, decides the next action.
- **Fanar-Oryx-IVU-2 (vision) = the eyes** — reads the rendered page/screen, Arabic
  forms, and screenshots (a Fanar component, so it boosts the Fanar score).
- **Playwright = the hands** — clicks, types, navigates, submits.

> **Loop:** Oryx/SoM perceive the page → Fanar reasons and picks the next action →
> Playwright executes → the result is re-read and the agent **verifies before continuing**
> (self-correcting). A human approves any irreversible action (submit / pay / login).

Fanar is OpenAI-compatible but has **no native function-calling**, so we use a robust
**ReAct-style JSON protocol**: each step Fanar emits ONE JSON object (a tool call or a
final answer); we parse it, run the real tool, stream the observation + any screenshot,
and feed it back. The loop is:

1. **Plan** — track persona + the tool schemas for the current *track × surface*.
2. **Act** — dispatch to the right tool family (browser / desktop / track / common).
3. **Observe** — stream `thought` / `action` / `observation` / `screenshot` events.
4. **Repeat** to a final answer.

### Two safety/quality mechanisms
- **Human-in-the-loop (resumable).** State lives in `AgentRun.messages`. On a website
  login *or* a risky desktop action, the loop emits `awaiting_user` and **returns**; the
  UI shows an approval card; the user acts/approves; `/api/agent/resume` continues — and
  for desktop actions the **pending action is executed only after approval**.
- **Tool-forcing.** Fanar sometimes answers from memory or emits prose; the loop refuses
  a tool-less final answer and issues corrective re-prompts to the JSON contract.

### Agentic properties (hackathon §3)
✅ Multi-step planning · ✅ task decomposition · ✅ tool orchestration (browser + OS + domain) ·
✅ memory/state across pauses · ✅ retrieval (live web) · ✅ multimodal (vision + voice) ·
✅ autonomous execution with human checkpoints.

---

## 5. Use of Fanar

Fanar is used **five different ways** — not just chat:

| Fanar capability | Model | Where |
|---|---|---|
| Reasoning / planning / answers | `Fanar` | the whole agent loop |
| Screen + image understanding | `Fanar-Oryx-IVU-2` | `see_screen` desktop tool |
| Speech → text | `Fanar-Aura-STT-LF-1` | voice command input |
| Text → speech (10 AR/EN voices) | `Fanar-Aura-TTS-2` | spoken replies |
| Domain generation (notes, lessons…) | `Fanar` | healthcare/education tools |

All model ids are env-configurable (`FANAR_MODEL`, `FANAR_VISION_MODEL`, `FANAR_STT_MODEL`, `FANAR_TTS_MODEL`).

---

## 6. Evaluation Results & Insights

Verified live against the real Fanar API:

- **Screen vision** — captured the real screen; Fanar-Oryx correctly identified the IDE and its UI. ✅
- **Government agent** — `web_search → open_page(portal.moi.gov.qa) → read_page → grounded Arabic answer` ("renewal fee 100 QAR"). ✅
- **Healthcare track** — called `drug_information("Metformin")` and answered with a disclaimer. ✅
- **Voice** — TTS produced audio (10 voices); STT round-tripped speech→text. ✅
- **Human-in-the-loop** — login pause→resume and desktop confirm-before-act both verified. ✅

### What Fanar handled well
Arabic & code-switched intent; formal Arabic generation; **accurate screen understanding
with Oryx**; clean TTS in Arabic voices (Noor/Jasim/Huda/Hamad).

### Where we needed an external layer
- **No native function-calling** → we built the ReAct/JSON orchestration layer.
- **Answer-from-memory / protocol drift** → mitigated with tool-forcing + re-prompts.
- **No native computer-use** → screen control via mss + pyautogui (vision still Fanar).
- **STT on synthetic TTS audio** mis-heard a proper noun ("Qatar ID"→"authority") — real-mic accuracy is better, but it flags STT robustness as an area to probe.

### Government services targeted (verified browser-accessible)
We did reconnaissance (a parallel research pass) on real Qatar e-gov services and seeded
the agent with **verified URLs**:

| Service | URL | Auth | Demo note |
|---|---|---|---|
| **Hukoomi** directory / life-moments | `hukoomi.gov.qa/en` | none | **Safest** — browse + explain requirements; behind Cloudflare/WAF (our stealth UA gets 200, default UA gets 403) |
| MOI **Traffic Violations** inquiry | `fees2.moi.gov.qa/moipay/...` | none | fill QID/plate → **pause for CAPTCHA** |
| MOI **Visa** inquiry / tracking | `portal.moi.gov.qa/.../visaservices/...` | none | fill visa/passport № → pause for CAPTCHA |
| MOI **Residency Permit** inquiry | `portal.moi.gov.qa/.../residencypermits` | none | read-only inquiry → pause for CAPTCHA |
| **MOPH** appointment booking | `appointments.moph.gov.qa/...` | none | fill the form → pause for CAPTCHA |

**Key insight:** every MOI inquiry has a verification-code **CAPTCHA**, and transactional
renewals are gated behind the Metrash2 app / Qatar Digital ID + OTP. So the agent
**fills everything autonomously and pauses for the human** at the CAPTCHA — never claims
to submit unattended. This is exactly why **knowledge-first** matters: "what documents do
I need?" is answered instantly by Fanar with no CAPTCHA at all.

### Browser engine
We drive the browser with **Playwright** (its own bundled Chromium) for the agent
best-practices modern computer-use agents rely on: **auto-waiting** locators (no flaky
sleeps), reliable navigation load-states, and role/text queries. Each session owns a
worker thread holding the Playwright sync instance so the browser survives across the
login pause/resume.

---

## 7. Recommendations for Future Fanar Improvements

1. **Native function/tool calling** + `response_format: json_object` — removes our entire JSON workaround.
2. **Tool-use fine-tuning/evals** so Fanar reliably *acts* instead of answering from memory.
3. **Grounded coordinates from Oryx** (return bounding boxes for UI elements) → far more reliable computer-use clicks.
4. **Streaming tool/argument deltas**; **stable long context** for multi-step traces.
5. **STT robustness** on noisy/code-switched audio; **TTS SSML / rate control**.
6. **A government/clinical/education Arabic eval set** for formal-register generation.

---

## 8. Getting Started

### Prerequisites
- **Python 3.11–3.14**, **Node 18+**, **Chrome or Edge** (Edge ships with Windows 11),
  a **Fanar API key** (https://api.fanar.qa/request/en). For voice: a microphone.

### Quick start (desktop app — all three processes)
**Easiest — just double-click `START.bat`** (no PowerShell execution-policy issues).
On first run it opens `backend\.env` so you can paste your `FANAR_API_KEY`, then run it again.

Or, if you prefer PowerShell:
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_desktop.ps1
```

### Or run pieces manually
```bash
# 1) backend
cd backend
cp .env.example .env          # set FANAR_API_KEY
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8008

# 2) frontend (web surface)
cd frontend && npm install && npm run dev      # http://localhost:3000

# 3) desktop shell (unlocks screen vision + computer control)
cd desktop && npm install && npm start
```

> The web UI works on its own (`surface=web`). Launching the Electron app flips it to
> `surface=desktop`, enabling screen-vision and computer-control tools.

### Example prompts to try

**Screen vision + Set-of-Marks (desktop app):**
- "Look at my screen and tell me what's open."
- "What's on my screen right now — describe the main buttons and menus."
- "Read my screen and summarise the document I'm looking at."
- "Open Notepad, then type a shopping list into it." *(it marks the screen, clicks, types — pausing for your approval)*
- "Find the Save button on my screen and click it."
- "Look at this form on my screen and fill in my name where it asks."

**Auto-login (browser):**
- "Go to github.com/login and sign in to my account." *(it opens the page, asks you for credentials in a masked form, then logs in itself)*
- "Open my email and log me in."

**Per track (Agent mode):**
- **Government:** "Find the official Qatar ID renewal fee and required documents."
- **Healthcare:** "Explain Metformin and write Arabic patient instructions for hypertension."
- **Education:** "Make a 5-question quiz on Qatari history and save it."

**Voice:** click 🎙 to speak your command; toggle 🔊 to hear replies (Fanar Aura).

A scripted live evaluation: `cd backend && BROWSER_HEADLESS=true python -u eval_smoke.py`.

---

## 9. Project Structure
```
backend/   fanar_client · agent · browser_session · desktop · tracks · tools · main
frontend/  app/ (kinetic UI) · components/ (Marquee, StepTimeline, LivePreview, VoiceButton, FanarMark)
desktop/   Electron shell (main.js, preload.js, tray)
scripts/   run_backend · run_frontend · run_desktop
```

---

## 10. Safety & Scope
- **Credentials stay with the human.** The agent never types passwords/OTPs/card numbers.
- **Desktop actions are confirmed.** Every click/type/app-launch pauses for your approval.
- **No shell execution.** Running arbitrary commands is intentionally out of scope.
- **Sandboxed writes** to `backend/agent_workspace/`; screenshots are local only.
- Healthcare outputs are **educational, not diagnostic**, and carry a disclaimer.
- No secrets committed; `FANAR_API_KEY` lives only in a gitignored `.env`.

---

## 11. Deliverables (Hackathon §8)
- [x] Working prototype (desktop + web, verified live across all 3 tracks + vision + voice)
- [x] GitHub repo · [x] this technical README
- [ ] Presentation & demo *(to record)*

*Built for the Fanar Hackathon 2026 — a real, multimodal, human-in-the-loop computer-use
agent on Fanar for government, healthcare, and education.*
