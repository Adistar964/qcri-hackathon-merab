"use client";

import { useEffect, useRef, useState } from "react";
import { FanarMark } from "@/components/FanarMark";
import { Marquee } from "@/components/Marquee";
import { TraceDisclosure } from "@/components/TraceDisclosure";
import { LivePreview, type LiveShot } from "@/components/LivePreview";
import { VoiceButton } from "@/components/VoiceButton";
import { ProfilePanel } from "@/components/ProfilePanel";
import { HistoryPanel } from "@/components/HistoryPanel";
import { Markdown } from "@/components/Markdown";
import { Icon } from "@/components/Icon";
import { makeT, type Lang } from "@/components/i18n";
import type { ChatMsg, PaymentField, Pending, ProfileField, SavedCredential, Surface } from "@/components/types";

type Mode = "chat" | "agent";

const TRACK = "general"; // one unified agent across government, healthcare & education

// Same-origin by default (Next rewrites /api/* to the backend). If the dev proxy buffers the SSE
// stream (steps appearing late), set NEXT_PUBLIC_BACKEND_URL=http://localhost:8008 to stream the
// trace DIRECTLY from the backend (CORS is open), so each step shows the instant it happens.
const API_BASE = process.env.NEXT_PUBLIC_BACKEND_URL ?? "";

type QuickAction = { label: string; labelAr: string; text: string; textAr: string };

// AGENT mode — live tasks the agent performs end-to-end (drives the browser, logs in, fills forms).
const AGENT_ACTIONS: QuickAction[] = [
  { label: "Healthcare", labelAr: "الصحة", text: "Check my upcoming hospital appointments", textAr: "اطّلع على مواعيدي القادمة في المستشفى" },
  { label: "Healthcare", labelAr: "الصحة", text: "Fetch me the list of my current medications", textAr: "أحضر لي قائمة أدويتي الحالية" },
  { label: "Gov · live", labelAr: "حكومي · مباشر", text: "Check my traffic violations", textAr: "تحقّق من مخالفاتي المرورية" },
  { label: "Gov · e-services", labelAr: "حكومي · خدمات", text: "Send my national address certificate to my email", textAr: "أرسل شهادة عنواني الوطني إلى بريدي الإلكتروني" },
  { label: "Gov · live", labelAr: "حكومي · مباشر", text: "Check my passport expiry date", textAr: "تحقّق من تاريخ انتهاء جواز سفري" },
  { label: "Healthcare", labelAr: "الصحة", text: "Show my latest lab results", textAr: "اعرض أحدث نتائج تحاليلي المخبرية" },
];

// CHAT mode — informational / how-to questions answered with knowledge (no browser actions).
const CHAT_ACTIONS: QuickAction[] = [
  { label: "Gov · knowledge", labelAr: "حكومي · معرفة", text: "What documents do I need for a family visit visa in Qatar?", textAr: "ما المستندات التي أحتاجها لتأشيرة زيارة عائلية في قطر؟" },
  { label: "How-to", labelAr: "كيف", text: "How do I renew my Qatar ID (QID)?", textAr: "كيف أجدّد بطاقتي القطرية (الرقم الشخصي)؟" },
  { label: "How-to", labelAr: "كيف", text: "How can I apply for permanent residency in Qatar?", textAr: "كيف يمكنني التقديم على الإقامة الدائمة في قطر؟" },
  { label: "Healthcare", labelAr: "الصحة", text: "What are the symptoms of vitamin D deficiency?", textAr: "ما أعراض نقص فيتامين د؟" },
  { label: "Education", labelAr: "التعليم", text: "Explain the water cycle for a 10-year-old", textAr: "اشرح دورة الماء لطفل عمره عشر سنوات" },
  { label: "How-to", labelAr: "كيف", text: "How do I renew my driving license in Qatar?", textAr: "كيف أجدّد رخصة القيادة في قطر؟" },
];

const MARQUEE = [
  "SCREEN VISION", "BROWSER CONTROL", "FORM FILLING", "VOICE I/O",
  "QATAR E-SERVICES", "DESKTOP CONTROL", "HUMAN-IN-THE-LOOP", "POWERED BY FANAR",
];

export default function Home() {
  const [surface, setSurface] = useState<Surface>("web");
  const [mode, setMode] = useState<Mode>("agent");
  const [voiceOut, setVoiceOut] = useState(false);
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [shot, setShot] = useState<LiveShot | null>(null);
  const [files, setFiles] = useState<{ name: string; bytes: number }[]>([]);
  const [profileOpen, setProfileOpen] = useState(false);
  const [profileFields, setProfileFields] = useState<ProfileField[]>([]);
  const [profileValues, setProfileValues] = useState<Record<string, string>>({});
  const [paymentFields, setPaymentFields] = useState<PaymentField[]>([]);
  const [paymentValues, setPaymentValues] = useState<Record<string, string>>({});
  const [credentials, setCredentials] = useState<SavedCredential[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [modeSwitch, setModeSwitch] = useState<Mode | null>(null);  // pending mode-switch confirmation
  const [lang, setLang] = useState<Lang>("en");                      // UI language ("en" | "ar")
  const t = makeT(lang);
  const rtl = lang === "ar";
  const sessionId = useRef("");
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // In agent mode a conversation is ONE task: once a prompt is sent, the input
  // locks and the user must start a new conversation. Chat mode stays multi-turn.
  const agentLocked = mode === "agent" && messages.some((m) => m.role === "user");

  useEffect(() => {
    sessionId.current = (globalThis.crypto?.randomUUID?.() as string) || `sess-${Date.now()}`;
    if (typeof window !== "undefined" && (window as any).fanarDesktop) setSurface("desktop");
    try {
      const saved = window.localStorage.getItem("fanar_lang");
      if (saved === "ar" || saved === "en") setLang(saved);
    } catch { /* ignore */ }
  }, []);

  // Persist the language choice and flip the whole document to RTL when Arabic.
  useEffect(() => {
    try { window.localStorage.setItem("fanar_lang", lang); } catch { /* ignore */ }
    if (typeof document !== "undefined") {
      document.documentElement.lang = lang;
      document.documentElement.dir = rtl ? "rtl" : "ltr";
    }
  }, [lang, rtl]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  // Load the saved-information profile + payment card (field defs + current values) once.
  useEffect(() => {
    (async () => {
      try {
        const j = await (await fetch("/api/profile")).json();
        setProfileFields(j.fields || []);
        setProfileValues(j.values || {});
      } catch { /* ignore */ }
      try {
        const p = await (await fetch("/api/payment")).json();
        setPaymentFields(p.fields || []);
        setPaymentValues(p.values || {});
      } catch { /* ignore */ }
      refreshCredentials();
    })();
  }, []);

  async function savePayment(values: Record<string, string>) {
    try {
      const r = await fetch("/api/payment", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values }),
      });
      const j = await r.json();
      setPaymentValues(j.values || {});
    } catch { /* ignore */ }
  }

  async function refreshCredentials() {
    try {
      const j = await (await fetch("/api/credentials")).json();
      setCredentials(j.credentials || []);
    } catch { /* ignore */ }
  }

  async function saveCredential(cred: Partial<SavedCredential>) {
    try {
      const r = await fetch("/api/credentials", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(cred),
      });
      const j = await r.json();
      setCredentials(j.credentials || []);
    } catch { /* ignore */ }
  }

  async function deleteCredential(host: string) {
    try {
      const r = await fetch(`/api/credentials/${encodeURIComponent(host)}`, { method: "DELETE" });
      const j = await r.json();
      setCredentials(j.credentials || []);
    } catch { /* ignore */ }
  }

  async function refreshProfile() {
    try {
      const j = await (await fetch("/api/profile")).json();
      setProfileValues(j.values || {});
    } catch { /* ignore */ }
  }

  async function saveProfile(values: Record<string, string>) {
    try {
      const r = await fetch("/api/profile", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values }),
      });
      const j = await r.json();
      setProfileValues(j.values || {});
    } catch { /* ignore */ }
  }

  async function refreshFiles() {
    try {
      const j = await (await fetch("/api/workspace")).json();
      setFiles((j.files || []).filter((f: any) => !f.name.endsWith(".png")));
    } catch { /* ignore */ }
  }

  function updateLast(fn: (m: ChatMsg) => ChatMsg) {
    setMessages((msgs) => {
      const copy = [...msgs];
      copy[copy.length - 1] = fn(copy[copy.length - 1]);
      return copy;
    });
  }

  async function speak(text: string) {
    if (!voiceOut || !text) return;
    try {
      const r = await fetch("/api/voice/speak", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text.slice(0, 500), voice: "Amelia" }),
      });
      if (!r.ok) return;
      const url = URL.createObjectURL(await r.blob());
      new Audio(url).play().catch(() => {});
    } catch { /* ignore */ }
  }

  function applyEvent(evt: any) {
    switch (evt.type) {
      case "delta": updateLast((m) => ({ ...m, content: m.content + evt.content })); break;
      case "thought": updateLast((m) => ({ ...m, trace: [...(m.trace ?? []), { type: "thought", content: evt.content }] })); break;
      case "action": updateLast((m) => ({ ...m, trace: [...(m.trace ?? []), { type: "action", tool: evt.tool, input: evt.input }] })); break;
      case "observation": updateLast((m) => ({ ...m, trace: [...(m.trace ?? []), { type: "observation", tool: evt.tool, result: evt.result }] })); break;
      case "screenshot":
        setShot({ url: evt.url, page_url: evt.page_url, title: evt.title });
        updateLast((m) => ({ ...m, trace: [...(m.trace ?? []), { type: "screenshot", url: evt.url, page_url: evt.page_url, title: evt.title }] }));
        break;
      case "awaiting_user":
        setPending({ reason: evt.reason, kind: evt.kind, fields: evt.fields, image: evt.image, details: evt.details });
        updateLast((m) => ({ ...m, trace: [...(m.trace ?? []), { type: "awaiting_user", reason: evt.reason, kind: evt.kind }] }));
        break;
      case "final": updateLast((m) => ({ ...m, content: evt.content })); speak(evt.content); break;
      case "stopped": updateLast((m) => ({ ...m, trace: [...(m.trace ?? []), { type: "stopped", content: evt.content || "Stopped." }] })); break;
      case "error": updateLast((m) => ({ ...m, trace: [...(m.trace ?? []), { type: "error", content: evt.content }] })); break;
    }
  }

  async function stream(url: string, body: object) {
    setBusy(true);
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    try {
      const res = await fetch(API_BASE + url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: ctrl.signal });
      if (!res.body) throw new Error("No response stream");
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() ?? "";
        for (const part of parts) {
          const line = part.trim();
          if (line.startsWith("data:")) applyEvent(JSON.parse(line.slice(5).trim()));
        }
      }
    } catch (err: any) {
      // A user-triggered Stop aborts the fetch — that's expected, not an error.
      if (err?.name !== "AbortError") {
        updateLast((m) => ({ ...m, content: m.content + `\n\n⚠ ${err.message ?? err}` }));
      }
    } finally {
      abortRef.current = null;
      setBusy(false);
      refreshFiles();
    }
  }

  // Stop button: forcefully halt the agent's current action.
  async function stopAgent() {
    try { abortRef.current?.abort(); } catch { /* ignore */ }
    try {
      await fetch("/api/agent/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId.current }),
      });
    } catch { /* ignore */ }
    setBusy(false);
    setPending(null);
    updateLast((m) => ({ ...m, trace: [...(m.trace ?? []), { type: "stopped", content: "Stopped by user." }] }));
  }

  async function send(text: string) {
    if (!text.trim() || busy) return;
    // Agent mode: only one prompt per conversation — start a New conversation for another.
    if (mode === "agent" && messages.some((m) => m.role === "user")) return;
    const history = messages.map((m) => ({ role: m.role, content: m.content }));
    setPending(null);
    setMessages((m) => [...m, { role: "user", content: text }, { role: "assistant", content: "", trace: mode === "agent" ? [] : undefined }]);
    setInput("");
    await stream(`/api/${mode}`, { message: text, history, session_id: sessionId.current, track: TRACK, surface, lang });
  }

  async function resume(note: string, approve: boolean) {
    if (busy) return;
    setPending(null);
    await stream("/api/agent/resume", { session_id: sessionId.current, note: approve ? note : `no — ${note}` });
  }

  async function submitCredentials(creds: Record<string, string>, remember: boolean) {
    if (busy) return;
    setPending(null);
    await stream("/api/agent/credentials", { session_id: sessionId.current, credentials: creds, remember });
    if (remember) refreshCredentials();
  }

  // Mid-task: user supplied a missing form value (e.g. QID / DOB). Optionally remember it.
  async function submitInputs(values: Record<string, string>, saveKeys: string[]) {
    if (busy) return;
    setPending(null);
    await stream("/api/agent/inputs", { session_id: sessionId.current, values, save_keys: saveKeys });
    if (saveKeys.length) refreshProfile();
  }

  // In-app captcha: the user typed the verification code shown in the app. The agent fills it
  // into the page and presses Submit itself.
  async function submitCaptcha(code: string) {
    if (busy) return;
    setPending(null);
    await stream("/api/agent/captcha", { session_id: sessionId.current, code });
  }

  // In-app OTP: the user typed the one-time code. The agent types it into the OTP field and
  // presses Continue itself, then resumes navigation.
  async function submitOtp(code: string) {
    if (busy) return;
    setPending(null);
    await stream("/api/agent/otp", { session_id: sessionId.current, code });
  }

  function clearUI() {
    setMessages([]);
    setPending(null);
    setShot(null);
    setFiles([]);
    setInput("");
  }

  // Close the current browser/agent session on the backend (best-effort).
  function closeSession(id: string) {
    fetch("/api/session/close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: id }),
    }).catch(() => {});
  }

  function newConversation() {
    if (busy) return;
    closeSession(sessionId.current);
    sessionId.current = (globalThis.crypto?.randomUUID?.() as string) || `sess-${Date.now()}`;
    clearUI();
  }

  // Chat and Agent are kept SEPARATE: switching modes always starts a NEW conversation rather
  // than reusing the current one. If a task is in progress (running, or has messages), confirm
  // first so the user doesn't lose work.
  function switchMode(target: Mode) {
    if (target === mode) return;
    const inProgress = busy || messages.length > 0;
    if (inProgress) {
      // Confirm via a styled in-app modal (matches the platform) instead of the browser's confirm().
      setModeSwitch(target);
      return;
    }
    doSwitchMode(target);
  }

  function doSwitchMode(target: Mode) {
    // Stop any running stream + close the backend session, then start fresh in the new mode.
    try { abortRef.current?.abort(); } catch { /* ignore */ }
    if (busy) {
      fetch("/api/agent/stop", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId.current }),
      }).catch(() => {});
    }
    closeSession(sessionId.current);
    sessionId.current = (globalThis.crypto?.randomUUID?.() as string) || `sess-${Date.now()}`;
    clearUI();
    setBusy(false);
    setMode(target);
    setModeSwitch(null);
  }

  function deleteConversation() {
    if (busy) return;
    closeSession(sessionId.current);
    clearUI();
  }

  const fresh = messages.length === 0;

  return (
    <div className="mx-auto flex h-screen max-w-[1400px] flex-col">
      {/* NAV */}
      <header className="drag-region flex items-center gap-4 border-b-2 border-line px-5 py-3">
        <FanarMark />
        <span className="ml-1 flex items-center gap-1.5 border-2 border-line px-2 py-1 text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">
          <Icon name={surface === "desktop" ? "monitor" : "globe"} size={12} />
          {surface === "desktop" ? t("badge.desktop") : t("badge.web")}
        </span>
        <div className="no-drag ml-auto flex items-center gap-2">
          <button
            onClick={() => setLang((l) => (l === "ar" ? "en" : "ar"))}
            title={t("nav.langTitle")}
            className="flex h-9 items-center gap-1.5 border-2 border-line px-3 text-xs font-bold uppercase tracking-tight text-muted-foreground transition hover:border-accent hover:text-accent"
          >
            <Icon name="globe" size={14} /> {lang === "ar" ? "EN" : "ع"}
          </button>
          <button
            onClick={() => setHistoryOpen(true)}
            title={t("nav.historyTitle")}
            className="flex h-9 items-center gap-1.5 border-2 border-line px-3 text-xs font-bold uppercase tracking-tight text-muted-foreground transition hover:border-accent hover:text-accent"
          >
            <Icon name="clock" size={14} /> {t("nav.history")}
          </button>
          <button
            onClick={() => setProfileOpen(true)}
            title={t("nav.myInfoTitle")}
            className="flex h-9 items-center gap-1.5 border-2 border-line px-3 text-xs font-bold uppercase tracking-tight text-muted-foreground transition hover:border-accent hover:text-accent"
          >
            <Icon name="id-card" size={14} /> {t("nav.myInfo")}
          </button>
          <button
            onClick={newConversation}
            disabled={busy}
            title={t("nav.newTitle")}
            className="flex h-9 items-center gap-1.5 border-2 border-line px-3 text-xs font-bold uppercase tracking-tight text-muted-foreground transition hover:border-accent hover:text-accent disabled:opacity-40"
          >
            <Icon name="plus" size={14} /> {t("nav.new")}
          </button>
          <button
            onClick={deleteConversation}
            disabled={busy || fresh}
            title={t("nav.deleteTitle")}
            className="flex h-9 w-9 items-center justify-center border-2 border-line text-muted-foreground transition hover:border-maroon hover:text-maroon disabled:opacity-40"
          >
            <Icon name="trash" size={15} />
          </button>
          <button
            onClick={() => setVoiceOut((v) => !v)}
            title={t("nav.voiceTitle")}
            className={`h-9 border-2 px-3 text-xs font-bold uppercase tracking-tight transition ${voiceOut ? "border-accent bg-accent text-accent-foreground" : "border-line text-muted-foreground hover:text-foreground"}`}
          >
            {t("nav.voice")} {voiceOut ? t("nav.on") : t("nav.off")}
          </button>
          <div className="flex border-2 border-line">
            {(["chat", "agent"] as Mode[]).map((m) => (
              <button key={m} onClick={() => switchMode(m)}
                className={`px-4 py-1.5 text-xs font-bold uppercase tracking-tight transition ${mode === m ? "bg-foreground text-bg" : "text-muted-foreground hover:text-foreground"}`}>
                {t(`mode.${m}`)}
              </button>
            ))}
          </div>
          {surface === "desktop" && (
            <div className="flex items-center gap-1">
              <button onClick={() => (window as any).fanarDesktop?.minimize?.()} title={t("nav.minimize")}
                className="flex h-9 w-9 items-center justify-center border-2 border-line text-muted-foreground hover:text-foreground">─</button>
              <button onClick={() => (window as any).fanarDesktop?.close?.()} title={t("nav.close")}
                className="flex h-9 w-9 items-center justify-center border-2 border-line text-muted-foreground hover:border-maroon hover:text-maroon"><Icon name="x" size={14} /></button>
            </div>
          )}
        </div>
      </header>

      {/* MARQUEE */}
      <Marquee items={MARQUEE} />

      {/* BODY */}
      <div className={`grid min-h-0 flex-1 ${mode === "agent" && !fresh ? "lg:grid-cols-[1fr_400px]" : "grid-cols-1"}`}>
        <div className="flex min-h-0 flex-col">
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-5 py-5">
            {fresh ? (
              <Hero mode={mode} surface={surface} onPick={send} lang={lang} />
            ) : (
              <div className="mx-auto max-w-3xl space-y-5">
                {messages.map((m, i) => (
                  <div key={i} className={`animate-fade-up ${m.role === "user" ? "flex justify-end" : ""}`}>
                    {m.role === "user" ? (
                      <div className="max-w-[85%] border-2 border-accent bg-accent/10 px-4 py-2.5 text-sm text-foreground" dir="auto">
                        {m.content}
                      </div>
                    ) : (
                      <div className={`${rtl ? "border-r-2 pr-4" : "border-l-2 pl-4"} border-line`}>
                        <div className="mb-1 text-[10px] font-bold uppercase tracking-[0.3em] text-accent">Fanar</div>
                        {m.trace && m.trace.length > 0 && (
                          <TraceDisclosure steps={m.trace} busy={busy && i === messages.length - 1} />
                        )}
                        {m.content ? (
                          <div className={m.trace?.length ? "mt-2" : ""}>
                            <Markdown content={m.content} />
                          </div>
                        ) : busy ? (
                          <div className="flex gap-1 py-2">
                            <span className="typing-dot h-2 w-2 bg-accent" /><span className="typing-dot h-2 w-2 bg-accent" /><span className="typing-dot h-2 w-2 bg-accent" />
                          </div>
                        ) : null}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* COMMAND BAR */}
          <div className="border-t-2 border-line px-5 py-4">
            {agentLocked ? (
              <div className="mx-auto flex max-w-3xl items-center gap-3 border-2 border-line bg-muted/40 px-4 py-3">
                <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
                  {t("locked.note")}
                </span>
                <button
                  onClick={newConversation}
                  disabled={busy}
                  className="ms-auto h-9 shrink-0 bg-accent px-4 text-xs font-bold uppercase tracking-tight text-accent-foreground transition hover:scale-[1.03] active:scale-95 disabled:opacity-40"
                >
                  <span className="flex items-center justify-center gap-1.5"><Icon name="plus" size={14} /> {t("locked.new")}</span>
                </button>
                {busy && (
                  <button
                    onClick={stopAgent}
                    className="h-9 shrink-0 border-2 border-maroon bg-maroon px-4 text-xs font-bold uppercase tracking-tight text-foreground transition hover:scale-[1.03] active:scale-95"
                  >
                    {t("cmd.stop")}
                  </button>
                )}
              </div>
            ) : (
              <div className="mx-auto flex max-w-3xl items-center gap-2">
                <VoiceButton onTranscript={(t) => setInput((p) => (p ? p + " " : "") + t)} disabled={busy} />
                <input
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); send(input); } }}
                  placeholder={mode === "agent" ? t("cmd.placeholderAgent") : t("cmd.placeholderChat")}
                  dir="auto"
                  className="h-12 flex-1 border-b-2 border-line bg-transparent px-1 text-base font-medium text-foreground placeholder-muted focus:border-accent focus:outline-none"
                />
                {busy ? (
                  <button onClick={stopAgent}
                    className="h-12 border-2 border-maroon bg-maroon px-6 text-sm font-bold uppercase tracking-tight text-foreground transition hover:scale-[1.03] active:scale-95">
                    {t("cmd.stop")}
                  </button>
                ) : (
                  <button onClick={() => send(input)} disabled={!input.trim()}
                    className="h-12 bg-accent px-6 text-sm font-bold uppercase tracking-tight text-accent-foreground transition hover:scale-[1.03] active:scale-95 disabled:opacity-40">
                    {t("cmd.send")}
                  </button>
                )}
              </div>
            )}
            <p className="mx-auto mt-2 max-w-3xl text-[10px] uppercase tracking-wider text-muted">
              {mode === "agent"
                ? surface === "desktop"
                  ? t("cmd.helperAgentDesktop")
                  : t("cmd.helperAgentWeb")
                : t("cmd.helperChat")}
            </p>
          </div>
        </div>

        {/* LIVE PREVIEW */}
        {mode === "agent" && !fresh && (
          <div className="min-h-0 overflow-y-auto border-l-2 border-line p-4">
            <LivePreview lang={lang} shot={shot} pending={pending} busy={busy} surface={surface} files={files} sessionId={sessionId.current} apiBase={API_BASE} onResume={resume} onCredentials={submitCredentials} onSubmitInputs={submitInputs} onSubmitCaptcha={submitCaptcha} onSubmitOtp={submitOtp} onCancel={() => resume("cancelled", false)} />
          </div>
        )}
      </div>

      <ProfilePanel
        open={profileOpen}
        lang={lang}
        fields={profileFields}
        values={profileValues}
        paymentFields={paymentFields}
        paymentValues={paymentValues}
        credentials={credentials}
        onClose={() => setProfileOpen(false)}
        onSave={saveProfile}
        onSavePayment={savePayment}
        onSaveCredential={saveCredential}
        onDeleteCredential={deleteCredential}
      />

      <HistoryPanel open={historyOpen} onClose={() => setHistoryOpen(false)} lang={lang} />

      {/* Styled mode-switch confirmation (matches the platform; replaces the browser confirm()) */}
      {modeSwitch && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-bg/80 p-4 backdrop-blur-sm"
             onClick={() => setModeSwitch(null)}>
          <div className="glow w-full max-w-md border-2 border-accent bg-surface p-6" onClick={(e) => e.stopPropagation()}>
            <div className="mb-2 text-xs font-bold uppercase tracking-[0.25em] text-accent">
              {t("switch.title", { mode: t(`mode.${modeSwitch}`) })}
            </div>
            <p className="text-sm leading-relaxed text-foreground">
              {busy ? t("switch.bodyBusy") : t("switch.body")}
            </p>
            <div className="mt-5 flex gap-2">
              <button
                onClick={() => doSwitchMode(modeSwitch)}
                className="flex-1 bg-accent px-4 py-3 text-sm font-bold uppercase tracking-tight text-accent-foreground transition hover:scale-[1.02] active:scale-95"
              >
                {t("switch.confirm")}
              </button>
              <button
                onClick={() => setModeSwitch(null)}
                className="border-2 border-line px-4 py-3 text-sm font-bold uppercase tracking-tight text-foreground transition hover:border-maroon hover:text-maroon"
              >
                {t("switch.cancel")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function Hero({ mode, surface, onPick, lang }: { mode: Mode; surface: Surface; onPick: (t: string) => void; lang: Lang }) {
  const t = makeT(lang);
  const ar = lang === "ar";
  return (
    <div className="mx-auto max-w-5xl py-6">
      <h1 className="font-display text-[clamp(2.5rem,9vw,7rem)] font-bold uppercase leading-[0.82] tracking-tightest text-foreground">
        {t("hero.title1")}
        <br />
        <span className="text-accent">{t("hero.title2")}</span>
      </h1>
      <p className="mt-5 max-w-2xl text-lg leading-tight text-muted-foreground">
        {t("hero.subtitle")}
      </p>

      <div className="mt-9 text-[10px] font-bold uppercase tracking-[0.3em] text-muted-foreground">
        {mode === "agent" ? t("hero.tryAgent") : t("hero.tryQuestion")}
      </div>
      <div className="mt-3 grid gap-px bg-line sm:grid-cols-2">
        {(mode === "agent" ? AGENT_ACTIONS : CHAT_ACTIONS).map((q, i) => (
          <button key={i} onClick={() => onPick(ar ? q.textAr : q.text)}
            className="group flex items-center gap-4 bg-bg px-5 py-4 text-start transition-colors duration-200 hover:bg-accent">
            <span className="font-display text-2xl font-bold leading-none text-muted group-hover:text-accent-foreground">{String(i + 1).padStart(2, "0")}</span>
            <span className="min-w-0">
              <span className="me-2 text-[10px] font-bold uppercase tracking-wide text-accent group-hover:text-accent-foreground">{ar ? q.labelAr : q.label}</span>
              <span className="text-[14px] font-medium text-foreground group-hover:text-accent-foreground" dir="auto">{ar ? q.textAr : q.text}</span>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
