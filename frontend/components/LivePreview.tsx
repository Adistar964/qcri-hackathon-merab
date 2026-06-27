"use client";

import { useEffect, useState } from "react";
import { Icon } from "./Icon";
import { makeT, type Lang } from "./i18n";
import type { InfoField, Pending, Surface } from "./types";

export interface LiveShot {
  url: string;
  page_url?: string;
  title?: string;
}

export function LivePreview({
  lang = "en",
  shot,
  pending,
  busy,
  surface,
  files,
  sessionId,
  apiBase,
  onResume,
  onCredentials,
  onSubmitInputs,
  onSubmitCaptcha,
  onSubmitOtp,
  onCancel,
}: {
  lang?: Lang;
  shot: LiveShot | null;
  pending: Pending | null;
  busy: boolean;
  surface: Surface;
  files: { name: string; bytes: number }[];
  sessionId?: string;
  apiBase?: string;
  onResume: (note: string, approve: boolean) => void;
  onCredentials: (creds: Record<string, string>, remember: boolean) => void;
  onSubmitInputs: (values: Record<string, string>, saveKeys: string[]) => void;
  onSubmitCaptcha: (code: string) => void;
  onSubmitOtp: (code: string) => void;
  onCancel: () => void;
}) {
  const t = makeT(lang);
  const [note, setNote] = useState("");
  const [tick, setTick] = useState(0);          // bumps the live-frame URL to re-fetch it
  const [liveUrl, setLiveUrl] = useState("");   // URL of the last frame that SUCCESSFULLY loaded
  const [maximized, setMaximized] = useState(false);
  const [creds, setCreds] = useState<Record<string, string>>({});
  const [info, setInfo] = useState<Record<string, string>>({});
  const [captcha, setCaptcha] = useState("");
  const [otp, setOtp] = useState("");
  const [remember, setRemember] = useState(true);
  const [rememberLogin, setRememberLogin] = useState(true);
  const isConfirm = pending?.kind === "confirm";
  const isCredentials = pending?.kind === "credentials";
  const isInfo = pending?.kind === "info";
  const isEdit = pending?.kind === "edit";
  const isCaptcha = pending?.kind === "captcha";
  const isOtp = pending?.kind === "otp";
  const isReview = pending?.kind === "review";
  const infoFields: InfoField[] = (isInfo || isEdit) ? ((pending?.fields ?? []) as InfoField[]) : [];

  // Pre-fill the edit form with each field's current value so the user only changes what they want.
  useEffect(() => {
    if (pending?.kind === "edit") {
      const init: Record<string, string> = {};
      for (const f of (pending.fields ?? []) as InfoField[]) {
        if (typeof f !== "string") init[f.key] = f.value ?? "";
      }
      setInfo(init);
    }
  }, [pending]);

  // Live browser view: while the agent is working (or paused at a gate, or the view is maximized),
  // re-fetch the current frame from the backend on a short interval so the embedded browser is live.
  const streaming = !!sessionId && (busy || !!pending || maximized);
  useEffect(() => {
    if (!streaming) return;
    const id = setInterval(() => setTick((x) => x + 1), 1100);
    return () => clearInterval(id);
  }, [streaming]);
  // PRELOAD-THEN-SWAP: fetch each candidate frame off-screen and only swap the visible <img> once
  // it has fully loaded. This (plus the backend reusing its last good frame) stops the embedded
  // browser from flashing/"refreshing" to the empty placeholder between polls.
  useEffect(() => {
    if (!streaming || !sessionId) return;
    const url = `${apiBase ?? ""}/api/agent/screen/${encodeURIComponent(sessionId)}?t=${tick}`;
    const img = new window.Image();
    img.onload = () => setLiveUrl(url);
    img.src = url;
    return () => { img.onload = null; };
  }, [tick, streaming, sessionId, apiBase]);
  // New conversation → drop the previous session's frame so it doesn't linger.
  useEffect(() => { setLiveUrl(""); }, [sessionId]);
  // Prefer the last successfully-loaded live frame; fall back to the agent's last action screenshot.
  const frameSrc = liveUrl || (shot ? `/api/screenshot/${shot.url}` : "");
  const placeholder = (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 px-8 text-center">
      <Icon name={surface === "desktop" ? "monitor" : "globe"} size={56} className="text-muted-foreground/30" />
      <p className="text-xs uppercase tracking-wide text-muted-foreground">
        {surface === "desktop" ? t("live.viewDesktop") : t("live.viewWeb")}
      </p>
    </div>
  );

  return (
    <aside className="flex w-full flex-col gap-4">
      {/* Live frame */}
      <div className="overflow-hidden rounded-2xl border border-line bg-surface/40 backdrop-blur-sm">
        <div className="flex items-center gap-2 border-b border-line px-3 py-2">
          <span className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-[0.25em] text-muted-foreground">
            <Icon name="dot" size={8} className={busy && !pending ? "text-accent" : "text-muted-foreground/40"} />
            {surface === "desktop" ? t("live.screen") : t("live.browser")}
          </span>
          <div className="ms-auto truncate text-[10px] text-muted-foreground/70">
            {shot?.page_url || shot?.title || t("live.standby")}
          </div>
          <span
            className={`rounded-full px-2 py-0.5 text-[9px] font-bold uppercase tracking-wide ${
              pending ? "bg-maroon text-foreground" : busy ? "bg-accent text-accent-foreground" : "bg-muted text-muted-foreground"
            }`}
          >
            {pending ? t("status.waiting") : busy ? t("status.live") : t("status.idle")}
          </span>
          <button
            onClick={() => setMaximized(true)}
            title={t("live.maximize")}
            className="flex h-6 w-6 items-center justify-center rounded-lg border border-line text-muted-foreground transition-all duration-200 ease-expo-out hover:border-accent/60 hover:text-accent"
          >
            <Icon name="maximize" size={12} />
          </button>
        </div>
        <div className="relative aspect-[16/10] overflow-hidden bg-bg">
          {placeholder}
          {frameSrc && (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={frameSrc} alt={shot?.title || "live frame"} className="absolute inset-0 h-full w-full object-cover object-top" />
          )}
          {busy && !pending && <div className="absolute inset-x-0 top-0 h-1"><div className="shimmer h-full w-full" /></div>}
        </div>
      </div>

      {/* Maximized live browser overlay */}
      {maximized && (
        <div className="fixed inset-0 z-[60] flex flex-col bg-black/95 backdrop-blur-md">
          <div className="flex items-center gap-3 border-b border-line px-5 py-3">
            <span className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-[0.25em] text-accent">
              <Icon name="dot" size={9} />
              {surface === "desktop" ? t("live.screen") : t("live.browser")}
            </span>
            <span className="truncate text-[11px] text-muted-foreground">{shot?.page_url || shot?.title || ""}</span>
            <span
              className={`ms-auto rounded-full px-2 py-0.5 text-[9px] font-bold uppercase tracking-wide ${
                pending ? "bg-maroon text-foreground" : busy ? "bg-accent text-accent-foreground" : "bg-muted text-muted-foreground"
              }`}
            >
              {pending ? t("status.waiting") : busy ? t("status.live") : t("status.idle")}
            </span>
            <button
              onClick={() => setMaximized(false)}
              className="flex items-center gap-1.5 rounded-lg border border-line px-3 py-1.5 text-[11px] font-bold uppercase tracking-tight text-foreground transition-all duration-200 ease-expo-out hover:border-maroon hover:text-maroon"
            >
              <Icon name="x" size={13} /> {t("live.close")}
            </button>
          </div>
          <div className="relative flex-1 bg-bg">
            {placeholder}
            {frameSrc && (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={frameSrc} alt={shot?.title || "live frame"} className="absolute inset-0 h-full w-full object-contain" />
            )}
          </div>
        </div>
      )}

      {/* Human-in-the-loop hand-off */}
      {pending && isCredentials && (
        <form
          onSubmit={(e) => { e.preventDefault(); onCredentials(creds, rememberLogin); setCreds({}); }}
          className="glow rounded-2xl border border-accent/40 bg-accent/[0.08] p-4 backdrop-blur-sm"
        >
          <div className="mb-1 flex items-center gap-1.5 text-xs font-bold uppercase tracking-[0.2em] text-accent"><Icon name="key" size={14} /> {t("creds.title")}</div>
          <p className="text-sm leading-relaxed text-foreground">{pending.reason}</p>
          <p className="mt-2 text-[11px] text-muted-foreground">
            {t("creds.masked")}
          </p>
          <div className="mt-3 space-y-2">
            {((pending.fields as string[]) || ["username", "password"]).map((f) => (
              <div key={f}>
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">{(() => { const k = "panel." + f.toLowerCase(); const v = t(k); return v === k ? f : v; })()}</label>
                <input
                  type={/pass|otp|pin|secret/i.test(f) ? "password" : "text"}
                  autoComplete="off"
                  value={creds[f] || ""}
                  onChange={(e) => setCreds((c) => ({ ...c, [f]: e.target.value }))}
                  className="w-full border-b border-line bg-transparent px-0 py-2 text-sm text-foreground focus:border-accent focus:outline-none"
                />
              </div>
            ))}
          </div>
          <label className="mt-3 flex cursor-pointer items-center gap-2 text-[11px] text-muted-foreground">
            <input type="checkbox" checked={rememberLogin} onChange={(e) => setRememberLogin(e.target.checked)} className="accent-accent" />
            {t("creds.rememberLogin")}
          </label>
          <div className="mt-3 flex gap-2">
            <button type="submit" disabled={busy}
              className="flex-1 rounded-xl bg-accent px-4 py-3 text-sm font-bold uppercase tracking-tight text-accent-foreground transition-all duration-200 ease-expo-out hover:opacity-90 active:scale-95 disabled:opacity-50">
              {t("creds.login")}
            </button>
            <button type="button" onClick={() => onResume("declined", false)} disabled={busy}
              className="rounded-xl border border-line px-4 py-3 text-sm font-bold uppercase tracking-tight text-foreground transition-all duration-200 ease-expo-out hover:border-maroon hover:text-maroon disabled:opacity-50">
              {t("btn.cancel")}
            </button>
          </div>
        </form>
      )}

      {/* Mid-task: agent needs form values — either missing saved info (kind=info) or the
          editable fields to review before saving an update (kind=edit, pre-filled). */}
      {pending && (isInfo || isEdit) && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const saveKeys = (!isEdit && remember) ? Object.keys(info).filter((k) => (info[k] || "").trim()) : [];
            onSubmitInputs(info, saveKeys);
            if (!isEdit) setInfo({});
          }}
          className="glow rounded-2xl border border-accent/40 bg-accent/[0.08] p-4 backdrop-blur-sm"
        >
          <div className="mb-1 flex items-center gap-1.5 text-xs font-bold uppercase tracking-[0.2em] text-accent"><Icon name={isEdit ? "pencil" : "clipboard"} size={14} /> {isEdit ? t("edit.title") : t("info.title")}</div>
          <p className="text-sm leading-relaxed text-foreground">{pending.reason}</p>
          {!isEdit && (
            <p className="mt-1 text-[11px] text-muted-foreground">
              {t("info.notFound")}
            </p>
          )}
          <div className="mt-3 space-y-3">
            {infoFields.map((f) => (
              <div key={f.key}>
                <label className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">
                  {f.label}
                  {f.format && <span className="ml-2 lowercase tracking-normal text-muted-foreground/60">({f.format})</span>}
                </label>
                {f.type === "select" && (f.options?.length ?? 0) > 0 ? (
                  <select
                    value={info[f.key] ?? ""}
                    onChange={(e) => setInfo((c) => ({ ...c, [f.key]: e.target.value }))}
                    className="mt-1 w-full border-b border-line bg-surface px-0 py-2 text-sm text-foreground focus:border-accent focus:outline-none"
                  >
                    {(f.options ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                ) : f.type === "checkbox" || f.type === "radio" ? (
                  <label className="mt-2 flex cursor-pointer items-center gap-2 text-sm text-foreground">
                    <input
                      type="checkbox"
                      checked={(info[f.key] ?? "").toLowerCase() === "true"}
                      onChange={(e) => setInfo((c) => ({ ...c, [f.key]: e.target.checked ? "true" : "false" }))}
                      className="accent-accent"
                    />
                    {f.label}
                  </label>
                ) : (
                  <input
                    type={f.type === "date" ? "date" : f.type === "email" ? "email" : f.type === "tel" ? "tel" : "text"}
                    placeholder={f.placeholder || ""}
                    autoComplete="off"
                    value={info[f.key] || ""}
                    onChange={(e) => setInfo((c) => ({ ...c, [f.key]: e.target.value }))}
                    className="mt-1 w-full border-b border-line bg-transparent px-0 py-2 text-sm text-foreground placeholder-muted-foreground/50 focus:border-accent focus:outline-none"
                  />
                )}
              </div>
            ))}
          </div>
          {!isEdit && (
            <label className="mt-3 flex cursor-pointer items-center gap-2 text-[11px] text-muted-foreground">
              <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} className="accent-accent" />
              {t("info.saveNext")}
            </label>
          )}
          <div className="mt-3 flex gap-2">
            <button
              type="submit"
              disabled={busy}
              className="flex-1 rounded-xl bg-accent px-4 py-3 text-sm font-bold uppercase tracking-tight text-accent-foreground transition-all duration-200 ease-expo-out hover:opacity-90 active:scale-95 disabled:opacity-50"
            >
              {isEdit ? t("edit.save") : t("btn.continue")}
            </button>
            <button
              type="button"
              onClick={() => { setInfo({}); onCancel(); }}
              disabled={busy}
              className="rounded-xl border border-line px-4 py-3 text-sm font-bold uppercase tracking-tight text-foreground transition-all duration-200 ease-expo-out hover:border-maroon hover:text-maroon disabled:opacity-50"
            >
              {t("btn.cancel")}
            </button>
          </div>
        </form>
      )}

      {/* In-app captcha: the captcha image from the site, with a box to type the code */}
      {pending && isCaptcha && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (pending.image) { onSubmitCaptcha(captcha); setCaptcha(""); }
            else { onResume("captcha solved", true); }
          }}
          className="glow rounded-2xl border border-accent/40 bg-accent/[0.08] p-4 backdrop-blur-sm"
        >
          <div className="mb-1 flex items-center gap-1.5 text-xs font-bold uppercase tracking-[0.2em] text-accent"><Icon name="hash" size={14} /> {t("captcha.title")}</div>
          <p className="text-sm leading-relaxed text-foreground">{pending.reason}</p>
          {pending.image && (
            <div className="mt-3 flex justify-center rounded-xl border border-line bg-white p-2">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={pending.image} alt="captcha" className="max-h-24 object-contain" />
            </div>
          )}
          {pending.image && (
            <input
              value={captcha}
              onChange={(e) => setCaptcha(e.target.value)}
              autoFocus
              autoComplete="off"
              inputMode="text"
              placeholder={t("captcha.placeholder")}
              className="mt-3 w-full border-b border-line bg-transparent px-0 py-2 text-center text-base tracking-[0.3em] text-foreground placeholder-muted-foreground/50 focus:border-accent focus:outline-none"
            />
          )}
          <div className="mt-3 flex gap-2">
            <button
              type="submit"
              disabled={busy || (!!pending.image && !captcha.trim())}
              className="flex-1 rounded-xl bg-accent px-4 py-3 text-sm font-bold uppercase tracking-tight text-accent-foreground transition-all duration-200 ease-expo-out hover:opacity-90 active:scale-95 disabled:opacity-50"
            >
              {pending.image ? t("captcha.fillSubmit") : t("captcha.imDone")}
            </button>
            <button
              type="button"
              onClick={() => { setCaptcha(""); onCancel(); }}
              disabled={busy}
              className="rounded-xl border border-line px-4 py-3 text-sm font-bold uppercase tracking-tight text-foreground transition-all duration-200 ease-expo-out hover:border-maroon hover:text-maroon disabled:opacity-50"
            >
              {t("btn.cancel")}
            </button>
          </div>
        </form>
      )}

      {/* In-app OTP: type the one-time code; the agent fills #otp-field and presses Continue */}
      {pending && isOtp && (
        <form
          onSubmit={(e) => { e.preventDefault(); if (otp.trim()) { onSubmitOtp(otp.trim()); setOtp(""); } }}
          className="glow rounded-2xl border border-accent/40 bg-accent/[0.08] p-4 backdrop-blur-sm"
        >
          <div className="mb-1 flex items-center gap-1.5 text-xs font-bold uppercase tracking-[0.2em] text-accent"><Icon name="hash" size={14} /> {t("otp.title")}</div>
          <p className="text-sm leading-relaxed text-foreground">{pending.reason}</p>
          <input
            value={otp}
            onChange={(e) => setOtp(e.target.value)}
            autoFocus
            autoComplete="one-time-code"
            inputMode="numeric"
            placeholder={t("otp.placeholder")}
            className="mt-3 w-full border-b border-line bg-transparent px-0 py-2 text-center text-base tracking-[0.3em] text-foreground placeholder-muted-foreground/50 focus:border-accent focus:outline-none"
          />
          <div className="mt-3 flex gap-2">
            <button
              type="submit"
              disabled={busy || !otp.trim()}
              className="flex-1 rounded-xl bg-accent px-4 py-3 text-sm font-bold uppercase tracking-tight text-accent-foreground transition-all duration-200 ease-expo-out hover:opacity-90 active:scale-95 disabled:opacity-50"
            >
              {t("otp.fillContinue")}
            </button>
            <button
              type="button"
              onClick={() => { setOtp(""); onCancel(); }}
              disabled={busy}
              className="rounded-xl border border-line px-4 py-3 text-sm font-bold uppercase tracking-tight text-foreground transition-all duration-200 ease-expo-out hover:border-maroon hover:text-maroon disabled:opacity-50"
            >
              {t("btn.cancel")}
            </button>
          </div>
        </form>
      )}

      {/* Payment review: show the fee + address pulled off the REVIEW PAYMENT page, then approve */}
      {pending && isReview && (
        <div className="glow rounded-2xl border border-accent/40 bg-accent/[0.08] p-4 backdrop-blur-sm">
          <div className="mb-1 flex items-center gap-1.5 text-xs font-bold uppercase tracking-[0.2em] text-accent"><Icon name="receipt" size={14} /> {t("review.title")}</div>
          <p className="text-sm leading-relaxed text-foreground">{pending.reason}</p>
          {pending.details?.total_fees && (
            <div className="mt-3 flex items-baseline justify-between rounded-xl border border-line bg-bg/60 px-3 py-2">
              <span className="text-[11px] font-bold uppercase tracking-wide text-muted-foreground">{t("review.totalFee")}</span>
              <span className="font-display text-xl font-bold text-accent">{pending.details.total_fees}</span>
            </div>
          )}
          {!!pending.details?.address?.length && (
            <div className="mt-3 rounded-xl border border-line bg-bg/60 px-3 py-2">
              <div className="mb-1 text-[11px] font-bold uppercase tracking-wide text-muted-foreground">{pending.details.title || t("review.details")}</div>
              <div className="grid grid-cols-1 gap-y-1">
                {pending.details.address.map((r, i) => (
                  <div key={i} className="flex justify-between gap-3 text-[13px]">
                    <span className="text-muted-foreground">{r.label}</span>
                    <span className="font-medium text-foreground text-end" dir="auto">{r.value}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          {!pending.details?.total_fees && !pending.details?.address?.length && pending.details?.raw && (
            <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap rounded-xl border border-line bg-bg/60 px-3 py-2 text-[12px] text-foreground">{pending.details.raw}</pre>
          )}
          {pending.details?.email && (
            <p className="mt-3 text-[12px] text-muted-foreground">{t("review.email")} <span className="font-medium text-foreground" dir="ltr">{pending.details.email}</span></p>
          )}
          <div className="mt-3 flex gap-2">
            <button
              onClick={() => onResume("approved — proceed with payment", true)}
              disabled={busy}
              className="flex-1 rounded-xl bg-accent px-4 py-3 text-sm font-bold uppercase tracking-tight text-accent-foreground transition-all duration-200 ease-expo-out hover:opacity-90 active:scale-95 disabled:opacity-50"
            >
              {t("review.approve")}
            </button>
            <button
              onClick={() => onResume("no — cancel the payment", false)}
              disabled={busy}
              className="rounded-xl border border-line px-4 py-3 text-sm font-bold uppercase tracking-tight text-foreground transition-all duration-200 ease-expo-out hover:border-maroon hover:text-maroon disabled:opacity-50"
            >
              {t("btn.cancel")}
            </button>
          </div>
        </div>
      )}

      {pending && !isCredentials && !isInfo && !isEdit && !isCaptcha && !isOtp && !isReview && (
        <div className="glow rounded-2xl border border-accent/40 bg-accent/[0.08] p-4 backdrop-blur-sm">
          <div className="mb-1 flex items-center gap-1.5 text-xs font-bold uppercase tracking-[0.2em] text-accent">
            <Icon name={isConfirm ? "shield-check" : "lock"} size={14} />
            {isConfirm ? (surface === "desktop" ? t("gen.approveAction") : t("gen.confirmToContinue")) : t("gen.loginNeeded")}
          </div>
          <p className="text-sm leading-relaxed text-foreground">{pending.reason}</p>
          <p className="mt-2 text-[11px] text-muted-foreground">
            {isConfirm
              ? (surface === "desktop" ? t("gen.confirmDesktopHint") : t("gen.confirmWebHint"))
              : t("gen.completeStepHint")}
          </p>
          {!isConfirm && (
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder={t("gen.optionalNote")}
              dir="auto"
              className="mt-3 w-full border-b border-line bg-transparent px-0 py-2 text-sm text-foreground placeholder-muted-foreground/50 focus:border-accent focus:outline-none"
            />
          )}
          <div className="mt-3 flex gap-2">
            <button
              onClick={() => { onResume(note || "approved", true); setNote(""); }}
              disabled={busy}
              className="flex-1 rounded-xl bg-accent px-4 py-3 text-sm font-bold uppercase tracking-tight text-accent-foreground transition-all duration-200 ease-expo-out hover:opacity-90 active:scale-95 disabled:opacity-50"
            >
              {isConfirm ? t("gen.approveRun") : t("gen.imDone")}
            </button>
            {isConfirm && (
              <button
                onClick={() => { onResume("declined", false); setNote(""); }}
                disabled={busy}
                className="rounded-xl border border-line px-4 py-3 text-sm font-bold uppercase tracking-tight text-foreground transition-all duration-200 ease-expo-out hover:border-maroon hover:text-maroon disabled:opacity-50"
              >
                {t("gen.skip")}
              </button>
            )}
          </div>
        </div>
      )}

      {/* Artefacts */}
      {files.length > 0 && (
        <div className="rounded-2xl border border-line bg-surface/30 p-3 backdrop-blur-sm">
          <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.25em] text-muted-foreground">{t("artefacts.title")}</div>
          <ul className="space-y-1">
            {files.map((f) => (
              <li key={f.name} className="flex justify-between text-[12px] text-muted-foreground">
                <span className="truncate"><span className="text-accent">▸</span> {f.name}</span>
                <span className="ml-2 shrink-0 text-muted-foreground/50">{f.bytes}B</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </aside>
  );
}
