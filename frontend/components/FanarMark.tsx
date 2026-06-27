// Fanar logo mark (gold-on-navy tile + calligraphic stroke) + wordmark.
export function FanarMark({ size = 34 }: { size?: number }) {
  return (
    <div className="flex items-center gap-3">
      <div
        className="relative flex items-center justify-center rounded-xl border border-line shadow-card"
        style={{ width: size, height: size, background: "linear-gradient(155deg,#2E3A4F,#0B0F1A)" }}
      >
        <svg viewBox="0 0 24 24" width={size * 0.6} height={size * 0.6} fill="none">
          <path
            d="M5 16c2.5 0 3.5-1.6 3.5-4.2 0-1.8 1-3.3 2.8-3.3 1.6 0 2.5 1.1 2.5 2.7 0 1.4-.8 2.2-1.8 2.2"
            stroke="#B08D57"
            strokeWidth="1.7"
            strokeLinecap="round"
          />
          <circle cx="17.5" cy="7.4" r="1" fill="#B08D57" />
        </svg>
      </div>
      <div className="leading-[0.9]">
        <div className="text-xl font-bold uppercase tracking-tightest text-foreground">
          Fan<span className="text-accent">ar</span>
        </div>
        <div className="text-[9px] font-medium uppercase tracking-[0.34em] text-muted-foreground">
          Agent
        </div>
      </div>
    </div>
  );
}
