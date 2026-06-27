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

  // Shared chrome-button styling (nav + window controls).
  const navBtn =
    "flex h-9 items-center gap-1.5 rounded-lg border border-line px-3 text-xs font-semibold uppercase tracking-tight text-muted-foreground transition-all duration-200 ease-expo-out hover:border-accent/60 hover:text-accent disabled:opacity-40";

  return (
    <div className="mx-auto flex h-screen max-w-[1400px] flex-col">
      {/* NAV */}
      <header className="drag-region flex items-center gap-4 border-b border-line px-5 py-3 backdrop-blur-md">
        <FanarMark />
        <span className="ml-1 flex items-center gap-1.5 rounded-full border border-line px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
          <Icon name={surface === "desktop" ? "monitor" : "globe"} size={12} />
          {surface === "desktop" ? t("badge.desktop") : t("badge.web")}
        </span>
        <div className="no-drag ml-auto flex items-center gap-2">
          <button
            onClick={() => setLang((l) => (l === "ar" ? "en" : "ar"))}
            title={t("nav.langTitle")}
            className={navBtn}
          >
            <Icon name="globe" size={14} /> {lang === "ar" ? "EN" : "ع"}
          </button>
          <button
            onClick={() => setHistoryOpen(true)}
            title={t("nav.historyTitle")}
            className={navBtn}
          >
            <Icon name="clock" size={14} /> {t("nav.history")}
          </button>
          <button
            onClick={() => setProfileOpen(true)}
            title={t("nav.myInfoTitle")}
            className={navBtn}
          >
            <Icon name="id-card" size={14} /> {t("nav.myInfo")}
          </button>
          <button
            onClick={newConversation}
            disabled={busy}
            title={t("nav.newTitle")}
            className={navBtn}
          >
            <Icon name="plus" size={14} /> {t("nav.new")}
          </button>
          <button
            onClick={deleteConversation}
            disabled={busy || fresh}
            title={t("nav.deleteTitle")}
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-line text-muted-foreground transition-all duration-200 ease-expo-out hover:border-maroon hover:text-maroon disabled:opacity-40"
          >
            <Icon name="trash" size={15} />
          </button>
          <button
            onClick={() => setVoiceOut((v) => !v)}
            title={t("nav.voiceTitle")}
            className={`flex h-9 items-center rounded-lg border px-3 text-xs font-semibold uppercase tracking-tight transition-all duration-200 ease-expo-out ${voiceOut ? "border-accent bg-accent text-accent-foreground" : "border-line text-muted-foreground hover:text-foreground"}`}
          >
            {t("nav.voice")} {voiceOut ? t("nav.on") : t("nav.off")}
          </button>
          <div className="flex rounded-lg border border-white/[0.06] bg-white/[0.02] p-1 backdrop-blur-sm">
            {(["chat", "agent"] as Mode[]).map((m) => (
              <button key={m} onClick={() => switchMode(m)}
                className={`rounded-md px-3 py-1.5 text-xs font-semibold uppercase tracking-wide transition-all duration-300 ${mode === m ? "bg-accent text-white shadow-accent-glow animate-glow-pulse" : "text-muted-foreground hover:scale-105 hover:bg-white/[0.05] hover:text-foreground"}`}>
                {t(`mode.${m}`)}
              </button>
            ))}
          </div>
          {surface === "desktop" && (
            <div className="flex items-center gap-1">
              <button onClick={() => (window as any).fanarDesktop?.minimize?.()} title={t("nav.minimize")}
                className="flex h-9 w-9 items-center justify-center rounded-lg border border-line text-muted-foreground hover:text-foreground">─</button>
              <button onClick={() => (window as any).fanarDesktop?.close?.()} title={t("nav.close")}
                className="flex h-9 w-9 items-center justify-center rounded-lg border border-line text-muted-foreground hover:border-maroon hover:text-maroon"><Icon name="x" size={14} /></button>
            </div>
          )}
        </div>
      </header>

      {/* MARQUEE */}
      <Marquee items={MARQUEE} />

      {/* BODY */}
      <div className={`grid min-h-0 flex-1 ${mode === "agent" && !fresh ? "lg:grid-cols-[1fr_400px]" : "grid-cols-1"}`}>
        <div className="flex min-h-0 flex-col">
          <div ref={scrollRef} className="no-scrollbar flex-1 overflow-y-auto px-5 py-5">
            {fresh ? (
              <Hero mode={mode} surface={surface} onPick={send} lang={lang} />
            ) : (
              <div className="mx-auto max-w-3xl space-y-5">
                {messages.map((m, i) => (
                  <div key={i} className={`animate-fade-up ${m.role === "user" ? "flex justify-end" : ""}`}>
                    {m.role === "user" ? (
                      <div className="max-w-[85%] rounded-2xl border border-accent/30 bg-accent/[0.08] px-4 py-2.5 text-sm text-foreground backdrop-blur-sm" dir="auto">
                        {m.content}
                      </div>
                    ) : (
                      <div className={`${rtl ? "border-r pr-4" : "border-l pl-4"} border-accent/40`}>
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
                            <span className="typing-dot h-2 w-2 rounded-full bg-accent" /><span className="typing-dot h-2 w-2 rounded-full bg-accent" /><span className="typing-dot h-2 w-2 rounded-full bg-accent" />
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
          <div className="border-t border-line px-5 py-4 backdrop-blur-md">
            {agentLocked ? (
              <div className="mx-auto flex max-w-3xl items-center gap-3 rounded-2xl border border-line bg-surface/40 px-4 py-3 backdrop-blur-sm">
                <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
                  {t("locked.note")}
                </span>
                <button
                  onClick={newConversation}
                  disabled={busy}
                  className="ms-auto h-9 shrink-0 rounded-xl bg-accent px-4 text-xs font-bold uppercase tracking-tight text-accent-foreground transition-all duration-200 ease-expo-out hover:opacity-90 hover:scale-[1.02] active:scale-95 disabled:opacity-40"
                >
                  <span className="flex items-center justify-center gap-1.5"><Icon name="plus" size={14} /> {t("locked.new")}</span>
                </button>
                {busy && (
                  <button
                    onClick={stopAgent}
                    className="group relative inline-flex h-9 shrink-0 items-center gap-2 overflow-hidden rounded-xl border border-maroon/25 px-4 text-xs font-medium text-[#D89A8A] transition-all duration-300 ease-expo-out [background:linear-gradient(180deg,rgba(183,112,127,0.16)_0%,rgba(183,112,127,0.09)_100%)] shadow-[0_1px_0_0_rgba(255,255,255,0.06)_inset] btn-sheen [--btn-sheen-tint:rgba(232,176,140,0.3)] [--btn-sheen-speed:5.5s] hover:border-maroon/45 hover:text-[#E9B58C] hover:[background:linear-gradient(180deg,rgba(183,112,127,0.24)_0%,rgba(183,112,127,0.14)_100%)] hover:[--btn-sheen-speed:2.6s] active:scale-[0.98]"
                  >
                    <span className="btn-stop-dot" aria-hidden="true" />
                    <span className="relative z-[2]">{t("cmd.stop")}</span>
                  </button>
                )}
              </div>
            ) : (
              <div className="mx-auto flex max-w-3xl items-center gap-2">
                <VoiceButton onTranscript={(t) => setInput((p) => (p ? p + " " : "") + t)} disabled={busy} />
                <div className="group relative flex-1">
                  <input
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); send(input); } }}
                    placeholder={mode === "agent" ? t("cmd.placeholderAgent") : t("cmd.placeholderChat")}
                    dir="auto"
                    className="h-14 w-full rounded-xl border border-white/[0.08] bg-gradient-to-b from-white/[0.08] to-white/[0.04] px-5 text-base font-medium text-foreground placeholder:text-foreground-subtle backdrop-blur-xl transition-all duration-300 focus:border-accent focus:bg-white/[0.1] focus:shadow-[0_0_0_3px_rgba(176,141,87,0.3),0_4px_20px_rgba(176,141,87,0.2)] focus:outline-none"
                  />
                  <div className="pointer-events-none absolute inset-x-0 -bottom-px h-px bg-gradient-to-r from-transparent via-accent to-transparent opacity-0 transition-opacity duration-500 group-focus-within:opacity-100" />
                </div>
                {busy ? (
                  <button onClick={stopAgent}
                    className="group relative inline-flex h-14 shrink-0 items-center gap-2.5 overflow-hidden rounded-xl border border-maroon/25 px-6 text-base font-medium text-[#D89A8A] transition-all duration-300 ease-expo-out [background:linear-gradient(180deg,rgba(183,112,127,0.16)_0%,rgba(183,112,127,0.09)_100%)] shadow-[0_1px_0_0_rgba(255,255,255,0.06)_inset] btn-sheen [--btn-sheen-tint:rgba(232,176,140,0.3)] [--btn-sheen-speed:5.5s] hover:border-maroon/45 hover:text-[#E9B58C] hover:[background:linear-gradient(180deg,rgba(183,112,127,0.24)_0%,rgba(183,112,127,0.14)_100%)] hover:[--btn-sheen-speed:2.6s] active:scale-[0.98]">
                    <span className="btn-stop-dot" aria-hidden="true" />
                    <span className="relative z-[2]">{t("cmd.stop")}</span>
                  </button>
                ) : (
                  <button onClick={() => send(input)} disabled={!input.trim()}
                    className="group relative h-14 shrink-0 overflow-hidden rounded-xl px-6 text-base font-medium text-accent-foreground transition-all duration-300 ease-expo-out [background:linear-gradient(180deg,#C6A268_0%,#B08D57_55%,#9E7C4A_100%)] shadow-[0_1px_0_0_rgba(255,255,255,0.22)_inset,0_-1px_0_0_rgba(0,0,0,0.18)_inset,0_2px_10px_-2px_rgba(176,141,87,0.45)] btn-sheen [--btn-sheen-tint:rgba(255,247,235,0.5)] enabled:btn-send-breathe hover:brightness-[1.06] hover:[--btn-sheen-speed:1.8s] active:scale-[0.98] active:brightness-100 disabled:cursor-not-allowed disabled:[background:#2E3A4F] disabled:text-foreground-subtle disabled:shadow-none disabled:brightness-100">
                    <span className="relative z-[2]">{t("cmd.send")}<span className="btn-send-arrow" aria-hidden="true">→</span></span>
                  </button>
                )}
              </div>
            )}
            <p className="mx-auto mt-2 max-w-3xl text-[10px] uppercase tracking-wider text-muted-foreground/60">
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
          <div className="min-h-0 overflow-y-auto border-l border-line p-4">
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
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-4 backdrop-blur-md"
             onClick={() => setModeSwitch(null)}>
          <div className="glow animate-scale-in w-full max-w-md rounded-2xl border border-accent/40 bg-surface/90 p-6 backdrop-blur-xl" onClick={(e) => e.stopPropagation()}>
            <div className="mb-2 text-xs font-bold uppercase tracking-[0.25em] text-accent">
              {t("switch.title", { mode: t(`mode.${modeSwitch}`) })}
            </div>
            <p className="text-sm leading-relaxed text-foreground">
              {busy ? t("switch.bodyBusy") : t("switch.body")}
            </p>
            <div className="mt-5 flex gap-2">
              <button
                onClick={() => doSwitchMode(modeSwitch)}
                className="flex-1 rounded-xl bg-accent px-4 py-3 text-sm font-bold uppercase tracking-tight text-accent-foreground transition-all duration-200 ease-expo-out hover:opacity-90 hover:scale-[1.02] active:scale-95"
              >
                {t("switch.confirm")}
              </button>
              <button
                onClick={() => setModeSwitch(null)}
                className="rounded-xl border border-line px-4 py-3 text-sm font-bold uppercase tracking-tight text-foreground transition-all duration-200 ease-expo-out hover:border-maroon hover:text-maroon"
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

// Letter-by-letter color cycle (MERAB signature). Spaces become non-breaking so the
// inline-block letters keep their width. Only used for Latin text (not Arabic).
function ColorShiftText({ text }: { text: string }) {
  return (
    <span className="text-color-shift">
      {text.split("").map((ch, i) => (
        <span key={i} style={{ animationDelay: `${(i * 0.1).toFixed(1)}s` }}>
          {ch === " " ? " " : ch}
        </span>
      ))}
    </span>
  );
}

function Hero({ mode, surface, onPick, lang }: { mode: Mode; surface: Surface; onPick: (t: string) => void; lang: Lang }) {
  const t = makeT(lang);
  const ar = lang === "ar";

  // Move the gold spotlight to follow the cursor inside a card.
  const onMove = (e: React.MouseEvent<HTMLElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    e.currentTarget.style.setProperty("--mouse-x", `${e.clientX - r.left}px`);
    e.currentTarget.style.setProperty("--mouse-y", `${e.clientY - r.top}px`);
  };

  return (
    <div className="mx-auto max-w-5xl py-10 pt-4">
      <h1 className="text-center font-display text-[clamp(2.5rem,9vw,7rem)] font-extrabold leading-[0.9] tracking-tightest text-foreground">
        {ar ? (
          <>
            {t("hero.title1")}
            <br />
            <span className="text-accent">{t("hero.title2")}</span>
          </>
        ) : (
          <>
            <ColorShiftText text={t("hero.title1")} />
            <br />
            <span className="text-accent">{t("hero.title2")}</span>
          </>
        )}
      </h1>

      <div className="mt-10 space-y-4">
        <div className="flex items-center gap-4">
          <div className="h-px flex-1 bg-gradient-to-r from-transparent via-white/10 to-transparent" />
          <span className="animate-pulse-glow font-mono text-xs uppercase tracking-widest text-foreground-subtle">
            {mode === "agent" ? t("hero.tryAgent") : t("hero.tryQuestion")}
          </span>
          <div className="h-px flex-1 bg-gradient-to-r from-transparent via-white/10 to-transparent" />
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          {(mode === "agent" ? AGENT_ACTIONS : CHAT_ACTIONS).map((q, i) => (
            <button
              key={i}
              onClick={() => onPick(ar ? q.textAr : q.text)}
              onMouseMove={onMove}
              style={{ animationDelay: `${(i * 0.08).toFixed(2)}s` }}
              className="card-spotlight group animate-fade-up cursor-pointer rounded-2xl border border-line bg-surface/40 p-5 text-start backdrop-blur-xl transition-all duration-300 ease-expo-out hover:-translate-y-1 hover:border-line-hover hover:shadow-card-hover"
            >
              <div className="relative z-10 flex items-start gap-4">
                <span className="font-display text-3xl font-bold leading-none text-foreground-subtle transition-all duration-200 group-hover:scale-110 group-hover:text-gradient-accent">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="font-mono text-[10px] uppercase tracking-widest text-accent group-hover:animate-shimmer">
                    {ar ? q.labelAr : q.label}
                  </div>
                  <p className="text-sm leading-relaxed text-foreground transition-colors duration-200 group-hover:text-white" dir="auto">
                    {ar ? q.textAr : q.text}
                  </p>
                </div>
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
