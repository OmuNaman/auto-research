import clsx from "clsx";
import type { Phase } from "@/lib/types";

const COLORS: Record<Phase, string> = {
  init: "bg-ink-600 text-ink-200",
  literature: "bg-blue-500/20 text-blue-300",
  design: "bg-purple-500/20 text-purple-300",
  provision: "bg-yellow-500/20 text-yellow-300",
  execute: "bg-orange-500/20 text-orange-300",
  analyze: "bg-pink-500/20 text-pink-300",
  write: "bg-cyan-500/20 text-cyan-300",
  done: "bg-accent/20 text-accent",
  failed: "bg-red-500/20 text-red-300",
};

export function PhaseBadge({ phase, dim = false }: { phase: Phase; dim?: boolean }) {
  return (
    <span className={clsx(
      "inline-flex items-center px-2 py-0.5 rounded text-xs font-mono uppercase tracking-wider",
      COLORS[phase],
      dim && "opacity-50",
    )}>
      {phase}
    </span>
  );
}

const ORDER: Phase[] = ["init", "literature", "design", "provision", "execute", "analyze", "write", "done"];

export function PhaseTimeline({ current }: { current: Phase }) {
  const ci = current === "failed" ? -1 : ORDER.indexOf(current);
  return (
    <div className="flex flex-wrap items-center gap-1">
      {ORDER.map((p, i) => (
        <PhaseBadge key={p} phase={p} dim={ci >= 0 && i > ci} />
      ))}
      {current === "failed" && <PhaseBadge phase="failed" />}
    </div>
  );
}
