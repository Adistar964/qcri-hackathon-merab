"use client";

import { useRef, useState } from "react";
import { Icon } from "./Icon";

// Records mic audio and sends it to Fanar STT (/api/voice/transcribe),
// returning the transcript via onTranscript.
export function VoiceButton({ onTranscript, disabled }: { onTranscript: (text: string) => void; disabled?: boolean }) {
  const [recording, setRecording] = useState(false);
  const [working, setWorking] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);

  async function start() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      chunksRef.current = [];
      rec.ondataavailable = (e) => e.data.size && chunksRef.current.push(e.data);
      rec.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setWorking(true);
        try {
          const blob = new Blob(chunksRef.current, { type: "audio/webm" });
          const fd = new FormData();
          fd.append("file", blob, "speech.webm");
          const r = await fetch("/api/voice/transcribe", { method: "POST", body: fd });
          const j = await r.json();
          if (j.text) onTranscript(j.text);
        } catch {
          /* ignore */
        } finally {
          setWorking(false);
        }
      };
      rec.start();
      recorderRef.current = rec;
      setRecording(true);
    } catch {
      alert("Microphone permission is required for voice input.");
    }
  }

  function stop() {
    recorderRef.current?.stop();
    setRecording(false);
  }

  return (
    <button
      onClick={recording ? stop : start}
      disabled={disabled || working}
      title="Speak (Fanar Aura STT)"
      className={`flex h-14 w-14 shrink-0 items-center justify-center rounded-xl border transition-all duration-200 ease-expo-out active:scale-95 disabled:opacity-40 ${
        recording ? "animate-pulse border-maroon bg-maroon text-foreground" : "border-line bg-surface/40 text-muted-foreground backdrop-blur-sm hover:border-accent/60 hover:text-accent"
      }`}
    >
      {working ? "…" : recording ? <span className="h-3 w-3 rounded-sm bg-current" /> : <Icon name="mic" size={18} />}
    </button>
  );
}
