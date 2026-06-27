// Infinite CSS marquee — a signature element of the kinetic system.
// Content is duplicated so the -50% translate loops seamlessly.
export function Marquee({
  items,
  fast = false,
  invert = false,
}: {
  items: string[];
  fast?: boolean;
  invert?: boolean;
}) {
  const row = [...items, ...items];
  return (
    <div
      className={`overflow-hidden border-y-2 border-line ${
        invert ? "bg-accent text-accent-foreground" : "bg-bg text-foreground"
      }`}
    >
      <div className={`marquee-track ${fast ? "animate-marquee-fast" : "animate-marquee"}`}>
        {row.map((item, i) => (
          <span
            key={i}
            className="flex items-center gap-5 px-5 py-2.5 text-sm font-bold uppercase tracking-tight"
          >
            {item}
            <span className={invert ? "text-accent-foreground/60" : "text-accent"}>✦</span>
          </span>
        ))}
      </div>
    </div>
  );
}
