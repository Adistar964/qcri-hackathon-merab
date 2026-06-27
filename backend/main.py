"""
FastAPI backend for Fanar Agent (desktop + multi-track + voice edition).

Endpoints:
  GET  /api/health             -> service + Fanar status
  GET  /api/capabilities       -> tracks, voices, model names
  POST /api/chat               -> plain streaming chat (SSE)
  POST /api/agent              -> agentic loop (track + surface aware); streams trace (SSE)
  POST /api/agent/resume       -> continue after a human pause (login / desktop approval)
  POST /api/voice/transcribe   -> Fanar STT: audio -> text
  POST /api/voice/speak        -> Fanar TTS: text -> audio/mpeg
  GET  /api/screenshot/{name}  -> PNG frame the agent captured (browser or screen)
  POST /api/session/close      -> close a session's browser
  GET  /api/workspace          -> artefacts the agent created

Run:  uvicorn main:app --reload --port 8008
"""

from __future__ import annotations

import base64
import json
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

load_dotenv()

import audit  # noqa: E402
import workflows  # noqa: E402
from agent import AgentRun  # noqa: E402
from browser_session import SHOTS_DIR, close_session, get_session  # noqa: E402
from fanar_client import FanarClient, FanarError  # noqa: E402
import credentials_store  # noqa: E402
import payment_store  # noqa: E402
from profile_store import (  # noqa: E402
    extract_from_image, field_meta, load_profile, save_profile, update_profile)
from tools import WORKSPACE  # noqa: E402
from tracks import TRACKS  # noqa: E402

app = FastAPI(title="Fanar Agent API", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

client = FanarClient()
RUNS: dict[str, AgentRun] = {}


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[Message] = []
    session_id: str = "default"
    track: str = "government"
    surface: str = "web"  # "web" | "desktop"
    lang: str = "en"      # UI language for what the agent SAYS — "en" | "ar" (the website itself
                          # is always driven in English; data/credentials/card values stay English)


class ResumeRequest(BaseModel):
    session_id: str = "default"
    note: str = ""


class SpeakRequest(BaseModel):
    text: str
    voice: str = "Noor"


class CredentialsRequest(BaseModel):
    session_id: str = "default"
    credentials: dict[str, str] = {}
    remember: bool = False


class SavedCredentialRequest(BaseModel):
    url: str = ""
    host: str = ""
    username: str = ""
    password: str = ""
    label: str = ""


class ProfileRequest(BaseModel):
    values: dict[str, str] = {}


class InputsRequest(BaseModel):
    session_id: str = "default"
    values: dict[str, str] = {}
    save_keys: list[str] = []


class CaptchaRequest(BaseModel):
    session_id: str = "default"
    code: str = ""


class OtpRequest(BaseModel):
    session_id: str = "default"
    code: str = ""


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# Headers that keep the SSE stream UN-buffered, so the UI shows each step the moment it happens
# (X-Accel-Buffering disables nginx/proxy buffering; no-transform stops content rewriting).
_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _agent_sse(run: AgentRun) -> StreamingResponse:
    """Stream a run's events as SSE, record every event into the audit transcript,
    and persist the conversation's audit trail when the stream ends."""
    def gen():
        # Send a comment line FIRST so the connection produces bytes immediately — this keeps the
        # dev proxy from dropping the socket ("socket hang up") while the first model call runs.
        yield ": connected\n\n"
        try:
            for event in run.run():
                if event.get("type") != "delta":   # keep the audit clean (final holds the text)
                    # Don't bloat the saved transcript with base64 captcha images.
                    run.record({k: v for k, v in event.items() if k != "image"} if "image" in event else event)
                yield _sse(event)
            yield _sse({"type": "done"})
        except Exception as exc:  # noqa: BLE001
            ev = {"type": "error", "content": f"Agent failure: {exc}"}
            run.record(ev)
            yield _sse(ev)
        finally:
            try:
                audit.save(run)
            except Exception:  # noqa: BLE001 — auditing must never break the response
                pass

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "fanar_configured": client.configured, "model": client.default_model, "base_url": client.base_url}


@app.get("/api/capabilities")
def capabilities() -> dict[str, Any]:
    return {
        "tracks": {k: {"label": v["label"], "tagline": v["tagline"], "quick_actions": v["quick_actions"]} for k, v in TRACKS.items()},
        "voices": client.list_voices(),
        "models": {"chat": client.default_model, "vision": client.vision_model, "stt": client.stt_model, "tts": client.tts_model},
    }


@app.post("/api/chat")
def chat(req: ChatRequest) -> StreamingResponse:
    lang_instr = (" Always reply in Modern Standard Arabic (العربية), regardless of the language the "
                  "user writes in. Keep numbers, emails, URLs and proper/portal names as-is."
                  if (req.lang or "en").lower() == "ar" else " Answer in the user's language.")
    messages = [{"role": "system", "content": "You are Fanar, a helpful assistant for Qatar. Answer clearly and concisely, using Markdown when it helps (headings, lists, bold)." + lang_instr}]
    messages += [{"role": m.role, "content": m.content} for m in req.history]
    messages.append({"role": "user", "content": req.message})
    # The full conversation we persist to history (prior turns + this exchange).
    convo = [{"role": m.role, "content": m.content} for m in req.history]
    convo.append({"role": "user", "content": req.message})

    def gen():
        yield ": connected\n\n"   # immediate byte so the dev proxy doesn't drop the socket
        answer = ""
        try:
            # Fanar GUARD: vet the input before answering (fail-open inside client.guard).
            verdict = client.guard(req.message)
            if not verdict.get("safe", True):
                answer = ("I can't help with that request, but I'm happy to help with anything "
                          "related to Qatar government services, healthcare, or education.")
                if (req.lang or "en").lower() == "ar":
                    answer = client.translate(answer, target="ar")
                yield _sse({"type": "delta", "content": answer})
                yield _sse({"type": "final", "content": answer})
                yield _sse({"type": "done"})
                return
            for delta in client.chat_stream(messages, temperature=0.3, max_tokens=1024):
                answer += delta
                yield _sse({"type": "delta", "content": delta})
            yield _sse({"type": "done"})
        except FanarError:
            # Fanar is down / timing out → a clear message instead of a silent failure.
            msg = ("⚠️ Fanar (the AI service) isn't responding right now — this is usually temporary. "
                   "Please try again in a moment.")
            if (req.lang or "en").lower() == "ar":
                try:
                    msg = client.translate(msg, target="ar")
                except FanarError:
                    pass
            yield _sse({"type": "delta", "content": msg})
            yield _sse({"type": "final", "content": msg})
            yield _sse({"type": "done"})
        finally:
            # Save the chat conversation so it shows up in History (best-effort).
            try:
                if answer.strip():
                    convo.append({"role": "assistant", "content": answer})
                    audit.save_chat(req.session_id, convo, track=req.track, surface=req.surface)
            except Exception:  # noqa: BLE001 — saving must never break the response
                pass

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.post("/api/agent")
def agent(req: ChatRequest) -> StreamingResponse:
    run = RUNS.get(req.session_id)
    # Rebuild the run if the track/surface/language changed mid-session.
    if run is None or run.track != req.track or run.surface != req.surface or run.lang != (req.lang or "en").lower():
        run = AgentRun(req.session_id, client, track=req.track, surface=req.surface, lang=req.lang)
        RUNS[req.session_id] = run
    run.add_user_message(req.message, history=[m.model_dump() for m in req.history])
    return _agent_sse(run)


@app.post("/api/agent/resume")
def agent_resume(req: ResumeRequest) -> StreamingResponse:
    run = RUNS.get(req.session_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No active agent run to resume.")
    run.resume(req.note)
    return _agent_sse(run)


@app.post("/api/agent/credentials")
def agent_credentials(req: CredentialsRequest) -> StreamingResponse:
    """Receive credentials the user typed into the masked UI form, then continue the run.
    Credentials are kept in memory on the run only and are never sent to the model or logged."""
    run = RUNS.get(req.session_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No active agent run.")
    run.set_credentials(req.credentials, remember=req.remember)
    return _agent_sse(run)


@app.post("/api/agent/inputs")
def agent_inputs(req: InputsRequest) -> StreamingResponse:
    """Receive form inputs (e.g. QID / DOB) the user supplied for the active workflow,
    optionally remember the chosen ones in their saved info, then continue the run."""
    run = RUNS.get(req.session_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No active agent run.")
    run.provide_inputs(req.values, req.save_keys)
    return _agent_sse(run)


@app.post("/api/agent/captcha")
def agent_captcha(req: CaptchaRequest) -> StreamingResponse:
    """Receive the verification code the user typed into the app for an in-app captcha, then
    continue: the agent fills the code into the page, presses Submit, and reads the result."""
    run = RUNS.get(req.session_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No active agent run.")
    run.provide_captcha(req.code)
    return _agent_sse(run)


@app.post("/api/agent/otp")
def agent_otp(req: OtpRequest) -> StreamingResponse:
    """Receive the one-time code (OTP) the user typed into the app, then continue: the agent
    types it into the OTP field, presses Continue in the OTP form, and resumes navigation.
    The code is kept on the run only and is never sent to the model or logged."""
    run = RUNS.get(req.session_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No active agent run.")
    run.provide_otp(req.code)
    return _agent_sse(run)


@app.post("/api/agent/stop")
def agent_stop(req: ResumeRequest) -> dict[str, Any]:
    """Stop button: forcefully halt the agent's current run at the next checkpoint."""
    run = RUNS.get(req.session_id)
    if run is None:
        return {"stopped": False, "reason": "no active run"}
    run.cancel()
    try:
        audit.save(run)
    except Exception:  # noqa: BLE001
        pass
    return {"stopped": True, "session_id": req.session_id}


@app.get("/api/workflows")
def get_workflows() -> dict[str, Any]:
    """The catalogue of individual action-workflows the agent can run (transparency)."""
    return {"workflows": workflows.list_workflows()}


@app.get("/api/history")
def get_history() -> dict[str, Any]:
    """Summaries of past conversations (audit trails), newest first."""
    return {"conversations": audit.list_all()}


@app.get("/api/history/{conv_id}")
def get_history_item(conv_id: str) -> dict[str, Any]:
    """The full audit trail (transcript) for one past conversation."""
    rec = audit.get(conv_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return rec


@app.delete("/api/history/{conv_id}")
def delete_history_item(conv_id: str) -> dict[str, Any]:
    return {"deleted": audit.delete(conv_id)}


@app.get("/api/profile")
def get_profile() -> dict[str, Any]:
    """Return the field definitions for the Saved Information panel plus current values."""
    return {"fields": field_meta(), "values": load_profile()}


@app.put("/api/profile")
def put_profile(req: ProfileRequest) -> dict[str, Any]:
    """Replace the saved profile (all fields optional; passwords are never stored here)."""
    return {"ok": True, "values": save_profile(req.values)}


@app.post("/api/profile/extract")
async def profile_extract(file: UploadFile = File(...), save: bool = Form(True)) -> dict[str, Any]:
    """Upload a QID photo; Fanar vision reads the fields to auto-fill the saved profile.
    When save=true the detected fields are merged into the profile and returned."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty image upload.")
    result = extract_from_image(client, data, mime_type=file.content_type or "image/jpeg")
    fields = result.get("fields", {})
    values = update_profile(fields) if (save and fields) else load_profile()
    return {"fields": fields, "values": values, "error": result.get("error")}


@app.get("/api/credentials")
def get_credentials() -> dict[str, Any]:
    """All saved logins (host → username/password), for the 'My Credentials' panel. Local only."""
    return {"credentials": credentials_store.list_all()}


@app.put("/api/credentials")
def put_credential(req: SavedCredentialRequest) -> dict[str, Any]:
    """Add or edit a saved login (mapped to the site host). Stored locally, never sent to the AI."""
    saved = credentials_store.upsert(req.model_dump())
    return {"ok": bool(saved), "credentials": credentials_store.list_all()}


@app.delete("/api/credentials/{host}")
def delete_credential(host: str) -> dict[str, Any]:
    return {"deleted": credentials_store.delete(host), "credentials": credentials_store.list_all()}


@app.get("/api/payment")
def get_payment() -> dict[str, Any]:
    """Field definitions for the Payment Card panel plus the current saved values (local only)."""
    return {"fields": payment_store.field_meta(), "values": payment_store.load_payment()}


@app.put("/api/payment")
def put_payment(req: ProfileRequest) -> dict[str, Any]:
    """Replace the saved payment card (all fields optional; stored locally, never sent to the AI)."""
    return {"ok": True, "values": payment_store.save_payment(req.values)}


@app.post("/api/voice/transcribe")
async def transcribe(file: UploadFile = File(...), language: str = Form("")) -> dict[str, Any]:
    audio = await file.read()
    try:
        text = client.transcribe(audio, filename=file.filename or "audio.webm", language=language or None)
    except FanarError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"text": text}


@app.post("/api/voice/speak")
def speak(req: SpeakRequest) -> Response:
    try:
        audio = client.speak(req.text, voice=req.voice)
    except FanarError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return Response(content=audio, media_type="audio/mpeg")


@app.get("/api/screenshot/{name}")
def screenshot(name: str) -> FileResponse:
    path = (SHOTS_DIR / name).resolve()
    if SHOTS_DIR not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Screenshot not found")
    return FileResponse(path, media_type="image/png")


# A 1×1 transparent PNG returned when there's no live frame yet (so the UI's <img> still loads and
# the placeholder behind it shows through, instead of a broken-image icon).
_BLANK_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


@app.get("/api/agent/screen/{session_id}")
def agent_screen(session_id: str) -> Response:
    """Live frame of the agent's browser (current viewport), polled by the in-app Live Browser view.
    Returns a transparent 1×1 PNG when there's no active page yet. Never cached."""
    sess = get_session(session_id, create=False)
    png = sess.live_screenshot() if sess else b""
    return Response(content=png or _BLANK_PNG, media_type="image/png",
                    headers={"Cache-Control": "no-store, max-age=0"})


@app.post("/api/session/close")
def session_close(req: ResumeRequest) -> dict[str, Any]:
    RUNS.pop(req.session_id, None)
    return {"closed": close_session(req.session_id), "session_id": req.session_id}


@app.get("/api/workspace")
def list_workspace() -> dict[str, Any]:
    files = [{"name": p.name, "bytes": p.stat().st_size} for p in sorted(WORKSPACE.glob("*")) if p.is_file()]
    return {"workspace": str(WORKSPACE), "files": files}
