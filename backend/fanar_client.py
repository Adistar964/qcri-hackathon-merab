"""
Fanar API client (OpenAI-compatible).

Fanar exposes an OpenAI-compatible REST API at https://api.fanar.qa/v1.
We deliberately keep this as a thin wrapper so the rest of the codebase only
depends on a small, well-typed surface. All model names and the base URL are
configurable via environment variables, because Fanar ships several specialised
models (chat, RAG, vision, speech) and the exact chat model id may change.

Models referenced in this project:
  - Fanar                : general Arabic/English chat (default)
  - Islamic-RAG / Sadiq  : retrieval-augmented answers
  - Fanar-Oryx-IVU       : vision / image understanding
  - Fanar-Aura-STT/TTS   : speech to text / text to speech
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Iterator

import httpx


class FanarError(RuntimeError):
    """Raised when the Fanar API returns an error or is unreachable."""


class FanarClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("FANAR_API_KEY", "")
        self.base_url = (base_url or os.getenv("FANAR_BASE_URL", "https://api.fanar.qa/v1")).rstrip("/")
        self.default_model = default_model or os.getenv("FANAR_MODEL", "Fanar")
        # A smaller/faster model drives the planning loop (tool selection); the
        # default (quality) model writes the final user-facing answer.
        self.planner_model = os.getenv("FANAR_PLANNER_MODEL", "Fanar-S-1-7B")
        # Specialised Fanar models (discovered from /v1/models).
        self.vision_model = os.getenv("FANAR_VISION_MODEL", "Fanar-Oryx-IVU-2")
        self.stt_model = os.getenv("FANAR_STT_MODEL", "Fanar-Aura-STT-LF-1")  # long-form: handles longer audio
        self.tts_model = os.getenv("FANAR_TTS_MODEL", "Fanar-Aura-TTS-2")
        # Safety/guard model used to vet a user's input BEFORE we act on it. Fanar's dedicated
        # guard model isn't exposed on this API tier, so we use the main chat model as a strict
        # safety classifier (the small planner model gave false positives on legitimate government
        # tasks). Fanar's own content filter (HTTP 400 safety) is also treated as a hard block.
        # Override with FANAR_GUARD_MODEL when a real guard model becomes available.
        self.guard_model = os.getenv("FANAR_GUARD_MODEL", "") or self.default_model
        # Network timeout for Fanar calls. Kept modest (not the old 120s) so that when Fanar's
        # chat endpoint is slow/down, a call FAILS FAST and the agent surfaces a clear "service
        # unavailable" message instead of hanging for two minutes with no response. Streaming
        # calls only hit this between chunks, so long answers still stream fine.
        # Generous enough that the FLAGSHIP model's big-context planning calls (24k-token workflow
        # context) are never cut off and wrongly judged "down" — that downgrade-to-a-weaker-model is
        # what made the agent wander after login. A genuine outage still fails within this bound.
        self.timeout = float(timeout if timeout is not None else os.getenv("FANAR_TIMEOUT", "75"))
        # Short, side-task calls (guard / translate) must never stall the start of a response, so
        # they use an even tighter timeout and fail open.
        self.fast_timeout = float(os.getenv("FANAR_FAST_TIMEOUT", "15"))
        # MODEL FALLBACK: if the requested model is genuinely unavailable (its chat endpoint times
        # out / 5xx — Fanar's models go down independently), transparently retry with the next
        # CAPABLE model so the app keeps working. The small "Fanar-S-1-7B" is deliberately NOT in
        # this chain — it's too weak for the agentic loop (it caused post-login wandering / logout
        # attempts), so we only ever fall back to the larger C-2-27B / C-1-8.7B. A failed model is
        # marked DOWN for FANAR_DOWN_TTL (circuit breaker). Configure via FANAR_FALLBACK_MODELS.
        self.fallback_models = [m.strip() for m in os.getenv(
            "FANAR_FALLBACK_MODELS", "Fanar-C-2-27B,Fanar-C-1-8.7B").split(",") if m.strip()]
        self._down_models: dict[str, float] = {}      # model -> monotonic time it was marked down
        self._down_ttl = float(os.getenv("FANAR_DOWN_TTL", "180"))   # re-probe a down model after this
        self._down_lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------ #
    # Model fallback helpers (circuit breaker)
    # ------------------------------------------------------------------ #
    def _candidates(self, requested: str) -> list[str]:
        """The ordered models to try for a call: the requested one first, then the configured
        fallbacks — skipping any marked DOWN within the last `_down_ttl` seconds (after which a
        model is re-probed, so the app recovers automatically when Fanar comes back). Never empty."""
        chain = [requested] + [m for m in self.fallback_models if m != requested]
        now = time.monotonic()
        with self._down_lock:
            live = [m for m in chain if now - self._down_models.get(m, -1e9) >= self._down_ttl]
        return live or [requested]

    @staticmethod
    def _is_transient(exc: "FanarError") -> bool:
        """True if the failure looks like the MODEL/endpoint being unavailable (timeout, network,
        5xx) — worth retrying on another model — vs a permanent 4xx (bad request / content filter)."""
        s = str(exc).lower()
        if "could not reach" in s or "timeout" in s or "timed out" in s:
            return True
        m = re.search(r"error (\d{3})", s)
        return bool(m and 500 <= int(m.group(1)) < 600)

    def _mark_down(self, model: str) -> None:
        with self._down_lock:
            self._down_models[model] = time.monotonic()

    # ------------------------------------------------------------------ #
    # Chat completions
    # ------------------------------------------------------------------ #
    def _chat_once(self, model: str, messages: list[dict[str, Any]], temperature: float,
                   max_tokens: int, timeout: float, **kwargs: Any) -> str:
        payload = {"model": model, "messages": messages, "temperature": temperature,
                   "max_tokens": max_tokens, **kwargs}
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(f"{self.base_url}/chat/completions",
                                   headers=self._headers(), json=payload)
        except httpx.HTTPError as exc:  # network / DNS / timeout
            raise FanarError(f"Could not reach Fanar API: {exc}") from exc
        if resp.status_code >= 400:
            raise FanarError(f"Fanar API error {resp.status_code}: {resp.text[:500]}")
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise FanarError(f"Unexpected Fanar response shape: {data}") from exc

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> str:
        """Single, non-streaming chat completion (with automatic model fallback). Returns the text."""
        cands = self._candidates(model or self.default_model)
        last_exc: FanarError | None = None
        for i, m in enumerate(cands):
            is_last = i == len(cands) - 1
            # Give EVERY model the FULL timeout — we only fall back on a GENUINE failure, never on a
            # merely-slow response. (Earlier a 15s "probe" cut off the flagship's big-context planning
            # calls, marked it down, and silently downgraded to a weaker model — the cause of the
            # post-login wandering. Guard/translate stay fast by passing a small `timeout` explicitly.)
            try:
                return self._chat_once(m, messages, temperature, max_tokens, timeout or self.timeout, **kwargs)
            except FanarError as exc:
                last_exc = exc
                if not is_last and self._is_transient(exc):
                    self._mark_down(m)
                    continue
                raise
        raise last_exc or FanarError("No Fanar model available.")

    # ------------------------------------------------------------------ #
    # Guard — vet a user's input for safety/appropriateness BEFORE acting
    # ------------------------------------------------------------------ #
    def guard(self, text: str) -> dict[str, Any]:
        """Classify a user message as appropriate or not for a Qatar government / healthcare /
        education services assistant. Returns {"safe": bool, "category": str, "reason": str}.

        FAIL-OPEN: if the guard model is unreachable or returns garbage we allow the request
        (safe=True) so safety-checking can never take the whole app down. Legitimate government
        tasks (renew QID, pay fines, traffic violations, certificates, appointments, …) are SAFE;
        only clearly harmful/illegal/abusive/unsafe content is flagged."""
        import json as _json

        text = (text or "").strip()
        if not text:
            return {"safe": True, "category": "none", "reason": "empty"}
        system = (
            "You are a strict content-safety classifier for a Qatar government, healthcare and "
            "education services assistant. Decide if the USER MESSAGE is appropriate to act on. "
            "UNSAFE = requests to do something illegal or harmful (violence, weapons, terrorism, "
            "drugs, hacking/fraud, self-harm, sexual content involving minors), hate or harassment, "
            "or attempts to misuse identity/payment data maliciously. SAFE = anything else, "
            "INCLUDING all ordinary government/health/education tasks (renew QID or licence, pay "
            "fines or fees, check traffic violations, address certificate, passport, appointments, "
            "medical records, school enrolment) and normal questions or small talk. When unsure, "
            "choose SAFE. Reply with ONLY a JSON object: "
            '{"safe": true|false, "category": "<short>", "reason": "<short>"}.'
        )
        try:
            raw = self.chat(
                [{"role": "system", "content": system},
                 {"role": "user", "content": f"USER MESSAGE:\n{text[:2000]}\n\nClassify it now."}],
                model=self.guard_model, temperature=0.0, max_tokens=80, timeout=self.fast_timeout)
        except FanarError as exc:
            # Fanar's OWN content filter rejecting the request (HTTP 400 safety / content_filter) is
            # itself a strong signal the input is unsafe → BLOCK. Only genuine connectivity errors
            # (network / timeout / 5xx) fail-open so safety-checking can't take the app down.
            msg = str(exc).lower()
            if "content_filter" in msg or "was filtered" in msg or '"type": "safety"' in msg:
                return {"safe": False, "category": "content_filter",
                        "reason": "Fanar's content filter flagged this request."}
            return {"safe": True, "category": "error", "reason": "guard unreachable (fail-open)"}
        # Parse the first JSON object out of the reply.
        start = raw.find("{") if isinstance(raw, str) else -1
        if start != -1:
            depth = 0
            for i in range(start, len(raw)):
                if raw[i] == "{":
                    depth += 1
                elif raw[i] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = _json.loads(raw[start:i + 1])
                            if isinstance(obj, dict) and "safe" in obj:
                                return {"safe": bool(obj.get("safe", True)),
                                        "category": str(obj.get("category", "")),
                                        "reason": str(obj.get("reason", ""))}
                        except _json.JSONDecodeError:
                            break
                        break
        # Couldn't parse a verdict → look for an explicit unsafe signal, else fail-open.
        low = (raw or "").lower()
        if '"safe": false' in low or '"safe":false' in low or "unsafe" in low:
            return {"safe": False, "category": "flagged", "reason": "classifier flagged the request"}
        return {"safe": True, "category": "unparsed", "reason": "fail-open"}

    # ------------------------------------------------------------------ #
    # Translate — localise short UI / step / question strings (AR <-> EN)
    # ------------------------------------------------------------------ #
    # Website button / portal / product names that must STAY in English even inside Arabic text —
    # the agent presses the real (English) button, so a step must read: اضغط على زر "Continue".
    _KEEP_LATIN = ("Continue", "Next", "Pay", "Submit", "Search", "Login", "Logout", "Cancel",
                   "Close", "OK", "Update", "Inquire", "Proceed to Payment", "NAPS", "HIMYAN",
                   "QPAY", "Tawtheeq", "Metrash2", "Metrash", "QID", "OTP", "English", "Print",
                   "Save", "Confirm", "Back", "Home Address", "Q.R.")

    def translate(self, text: str, target: str = "ar") -> str:
        """Translate a short user-facing string into `target` (default Arabic) with the main Fanar
        model (excellent at Arabic; the dedicated MT model isn't on the chat/completions allow-list).

        Used to localise the agent's STEP descriptions and QUESTIONS to the user — NOT the website
        itself (button labels we click, field values, credentials, card data stay English). Keeps
        numbers, %/currency, emails, URLs, Markdown and any **bold**/`code` intact, and leaves the
        website's English button/portal names (Continue, Pay, NAPS, Tawtheeq, QID…) untouched so the
        instruction still points at the real (English) control. FAIL-OPEN: returns the original text
        on any error so localisation can never break the response."""
        src = (text or "").strip()
        if not src:
            return text
        tgt = {"ar": "Arabic", "en": "English"}.get(target, target)
        keep = ", ".join(self._KEEP_LATIN)
        system = (
            f"You are a professional UI localiser. Translate the user's text into {tgt}. "
            "Output ONLY the translation — no quotes, no notes, no transliteration. "
            "Preserve numbers, digits, currency, percentages, emails, URLs and any Markdown "
            "formatting (**bold**, `code`, lists, tables) EXACTLY. "
            "Keep these website button / portal / product names in their ORIGINAL Latin script "
            "(do NOT translate or transliterate them), because the user must click the real control "
            f"with that exact English label: {keep}. For example, translate "
            '\'Click the "Continue" button\' to Arabic but keep the word Continue in Latin. '
            "Keep it natural, concise and in the same tone."
        )
        try:
            out = self.chat(
                [{"role": "system", "content": system},
                 {"role": "user", "content": src}],
                model=self.default_model, temperature=0.0, max_tokens=max(128, len(src) * 2),
                timeout=self.fast_timeout)
        except FanarError:
            return text
        out = (out or "").strip().strip('"').strip()
        return out or text

    # ------------------------------------------------------------------ #
    # Vision (Fanar-Oryx-IVU-2) — understand an image / screenshot
    # ------------------------------------------------------------------ #
    def see_image(self, image_b64: str, prompt: str, max_tokens: int = 400,
                  mime_type: str = "image/png") -> str:
        """Ask Fanar's vision model about a base64 PNG/JPEG image. Returns text."""
        payload = {
            "model": self.vision_model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}},
                ],
            }],
            "max_tokens": max_tokens,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(f"{self.base_url}/chat/completions", headers=self._headers(), json=payload)
        except httpx.HTTPError as exc:
            raise FanarError(f"Could not reach Fanar vision API: {exc}") from exc
        if resp.status_code >= 400:
            raise FanarError(f"Fanar vision error {resp.status_code}: {resp.text[:300]}")
        return resp.json()["choices"][0]["message"]["content"]

    # ------------------------------------------------------------------ #
    # Speech — Fanar-Aura STT (transcribe) and TTS (speak)
    # ------------------------------------------------------------------ #
    def transcribe(self, audio_bytes: bytes, filename: str = "audio.webm", language: str | None = None) -> str:
        """Transcribe speech to text via Fanar-Aura STT (OpenAI-compatible)."""
        data = {"model": self.stt_model}
        if language:
            data["language"] = language
        files = {"file": (filename, audio_bytes, "application/octet-stream")}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    data=data,
                    files=files,
                )
        except httpx.HTTPError as exc:
            raise FanarError(f"Could not reach Fanar STT API: {exc}") from exc
        if resp.status_code >= 400:
            raise FanarError(f"Fanar STT error {resp.status_code}: {resp.text[:300]}")
        out = resp.json()
        return out.get("text", "") if isinstance(out, dict) else str(out)

    def list_voices(self) -> list[dict[str, Any]]:
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(f"{self.base_url}/audio/voices", headers=self._headers())
            if resp.status_code >= 400:
                return []
            return resp.json().get("voices", [])
        except httpx.HTTPError:
            return []

    def speak(self, text: str, voice: str = "Noor") -> bytes:
        """Synthesize speech via Fanar-Aura TTS. Returns audio/mpeg bytes."""
        payload = {"model": self.tts_model, "input": text, "voice": voice}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(f"{self.base_url}/audio/speech", headers=self._headers(), json=payload)
        except httpx.HTTPError as exc:
            raise FanarError(f"Could not reach Fanar TTS API: {exc}") from exc
        if resp.status_code >= 400:
            raise FanarError(f"Fanar TTS error {resp.status_code}: {resp.text[:300]}")
        return resp.content

    def _chat_stream_once(self, model: str, messages: list[dict[str, Any]], temperature: float,
                          max_tokens: int, timeout: float, **kwargs: Any) -> Iterator[str]:
        import json
        payload = {"model": model, "messages": messages, "temperature": temperature,
                   "max_tokens": max_tokens, "stream": True, **kwargs}
        try:
            with httpx.Client(timeout=timeout) as client:
                with client.stream("POST", f"{self.base_url}/chat/completions",
                                   headers=self._headers(), json=payload) as resp:
                    if resp.status_code >= 400:
                        body = resp.read().decode("utf-8", "replace")
                        raise FanarError(f"Fanar API error {resp.status_code}: {body[:500]}")
                    for line in resp.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[len("data:"):].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            delta = chunk["choices"][0]["delta"].get("content")
                            if delta:
                                yield delta
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
        except httpx.HTTPError as exc:
            raise FanarError(f"Could not reach Fanar API: {exc}") from exc

    def chat_stream(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
        **kwargs: Any,
    ) -> Iterator[str]:
        """Streaming chat completion (with automatic model fallback). Yields deltas as they arrive.
        We only fall back to another model if the current one fails BEFORE emitting any tokens — once
        text has streamed we commit to that model (a mid-stream error is re-raised, not retried)."""
        cands = self._candidates(model or self.default_model)
        last_exc: FanarError | None = None
        for i, m in enumerate(cands):
            is_last = i == len(cands) - 1
            yielded = False
            try:
                for delta in self._chat_stream_once(m, messages, temperature, max_tokens, self.timeout, **kwargs):
                    yielded = True
                    yield delta
                return
            except FanarError as exc:
                last_exc = exc
                if not yielded and not is_last and self._is_transient(exc):
                    self._mark_down(m)
                    continue
                raise
        if last_exc:
            raise last_exc
