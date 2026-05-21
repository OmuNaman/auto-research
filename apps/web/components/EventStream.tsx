import clsx from "clsx";
import type { Ev } from "@/lib/types";

function fmtTs(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toISOString().slice(11, 19);
}

const TYPE_COLOR: Record<string, string> = {
  "phase.transition": "text-blue-300",
  "llm.message_delta": "text-ink-300",
  "llm.tool_call": "text-yellow-300",
  "tool.result": "text-orange-300",
  "citation.added": "text-cyan-300",
  "pod.provisioned": "text-purple-300",
  "pod.status": "text-purple-200",
  "pod.log": "text-ink-300",
  "experiment.metric": "text-pink-300",
  "file.written": "text-green-300",
  "cost.tick": "text-yellow-200",
  "analyst.verdict": "text-accent",
  "error": "text-red-300",
};

function summarize(ev: Ev): string {
  switch (ev.type) {
    case "phase.transition": return `${ev.from_phase} → ${ev.to_phase} (${ev.reason || "—"})`;
    case "llm.message_delta": return `${ev.agent}: ${ev.text_delta.slice(0, 120)}`;
    case "llm.tool_call": return `${ev.agent} calls ${ev.tool}(${JSON.stringify(ev.input_json).slice(0, 80)})`;
    case "tool.result": return `${ev.tool} ${ev.ok ? "ok" : "ERR"} (${ev.duration_ms}ms)${ev.error ? ` ${ev.error}` : ""}`;
    case "citation.added": return `+ ${ev.key}: ${ev.title.slice(0, 90)}`;
    case "pod.provisioned": return `pod ${ev.pod_id} on ${ev.gpu} @ $${ev.usd_per_hour.toFixed(2)}/hr`;
    case "pod.status": return `pod ${ev.pod_id}: ${ev.status}`;
    case "pod.log": return `${ev.pod_id} | ${ev.lines.slice(0,2).join(" / ").slice(0, 200)}`;
    case "experiment.metric": return `${ev.experiment_id} step=${ev.step} ${JSON.stringify(ev.metrics)}`;
    case "file.written": return `wrote ${ev.path} (${ev.kind}, ${ev.size_bytes}B)`;
    case "cost.tick": return `$${ev.total_usd.toFixed(4)}`;
    case "analyst.verdict": return `verdict=${ev.verdict} — ${ev.rationale.slice(0, 100)}`;
    case "error": return `[${ev.where}] ${ev.message.slice(0, 200)}`;
    default: return JSON.stringify(ev).slice(0, 200);
  }
}

export function EventStream({ events, filter }: { events: Ev[]; filter?: (e: Ev) => boolean }) {
  const filtered = filter ? events.filter(filter) : events;
  return (
    <div className="font-mono text-xs h-[60vh] overflow-auto border border-ink-700 rounded-lg bg-ink-900 p-2">
      {filtered.slice(-500).reverse().map(ev => (
        <div key={ev.id} className="flex gap-2 py-0.5 hover:bg-ink-800/50 rounded px-1">
          <span className="text-ink-400 shrink-0">{fmtTs(ev.ts)}</span>
          <span className={clsx("shrink-0 w-32", TYPE_COLOR[ev.type] || "text-ink-300")}>
            {ev.type}
          </span>
          <span className="text-ink-200 truncate">{summarize(ev)}</span>
        </div>
      ))}
      {filtered.length === 0 && (
        <div className="text-ink-400 text-center py-12">waiting for events…</div>
      )}
    </div>
  );
}
