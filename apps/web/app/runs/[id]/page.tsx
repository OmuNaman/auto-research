"use client";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { cancelRun, getRun, fileUrl } from "@/lib/api";
import { useRunStream } from "@/lib/eventStream";
import { PhaseBadge, PhaseTimeline } from "@/components/PhaseBadge";
import { CostTicker } from "@/components/CostTicker";
import { PodCard } from "@/components/PodCard";
import { CitationCard } from "@/components/CitationCard";
import { MetricChart } from "@/components/MetricChart";
import { EventStream } from "@/components/EventStream";
import { ArtifactViewer } from "@/components/ArtifactViewer";
import type { Run } from "@/lib/types";

type Tab = "stream" | "citations" | "pods" | "metrics" | "artifacts" | "thinking";

export default function RunMonitor() {
  const params = useParams<{ id: string }>();
  const runId = params.id;
  const stream = useRunStream(runId);
  const [run, setRun] = useState<Run | null>(null);
  const [tab, setTab] = useState<Tab>("stream");
  const [selectedFile, setSelectedFile] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = () => getRun(runId).then(r => { if (!cancelled) setRun(r); }).catch(() => {});
    tick();
    const h = setInterval(tick, 4000);
    return () => { cancelled = true; clearInterval(h); };
  }, [runId]);

  const phase = run?.current_phase || stream.currentPhase;
  const cost = Math.max(run?.total_cost_usd || 0, stream.cost);

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-4">
          <div className="font-mono text-sm text-ink-300">{runId.slice(0, 16)}…</div>
          <PhaseBadge phase={phase} />
          <span className={`text-xs px-2 py-0.5 rounded ${stream.connected ? "bg-accent/20 text-accent" : "bg-ink-700 text-ink-400"}`}>
            {stream.connected ? "● live" : "○ disconnected"}
          </span>
        </div>
        <div className="flex items-center gap-4">
          <CostTicker usd={cost} />
          <button
            onClick={() => cancelRun(runId)}
            className="text-xs text-ink-400 hover:text-red-300 border border-ink-700 px-3 py-1 rounded"
          >
            Cancel
          </button>
        </div>
      </div>

      <div className="mb-4">
        <PhaseTimeline current={phase} />
      </div>

      {stream.verdicts.length > 0 && (
        <div className="border border-accent/40 bg-accent/5 rounded-lg p-3 mb-4 text-sm">
          {stream.verdicts.slice(-1).map((v, i) => (
            <div key={i}>
              <span className="font-mono text-accent uppercase tracking-wider text-xs">
                verdict round {v.round}: {v.verdict}
              </span>
              <div className="text-ink-200 mt-1">{v.rationale}</div>
            </div>
          ))}
        </div>
      )}

      <div className="border-b border-ink-700 flex gap-1 mb-4">
        {(["stream","thinking","citations","pods","metrics","artifacts"] as Tab[]).map(t => {
          const counts: Record<Tab, number | string> = {
            stream: stream.events.length,
            thinking: Object.keys(stream.messagesByAgent).length,
            citations: Object.keys(stream.citations).length,
            pods: Object.keys(stream.pods).length,
            metrics: Object.keys(stream.metrics).length,
            artifacts: stream.files.length,
          };
          return (
            <button key={t} onClick={() => setTab(t)}
              className={`px-4 py-2 text-sm border-b-2 -mb-px ${
                tab === t ? "border-accent text-ink-100" : "border-transparent text-ink-400 hover:text-ink-200"}`}>
              {t} <span className="text-ink-500 ml-1 tabular-nums">({counts[t]})</span>
            </button>
          );
        })}
      </div>

      {tab === "stream" && <EventStream events={stream.events} />}

      {tab === "thinking" && (
        <div className="space-y-4">
          {Object.entries(stream.messagesByAgent).map(([agent, msgs]) => (
            <div key={agent} className="border border-ink-700 rounded-lg bg-ink-800 p-3">
              <div className="text-xs font-mono text-accent uppercase tracking-wider mb-2">{agent}</div>
              <div className="space-y-3 max-h-[60vh] overflow-auto">
                {msgs.map(m => (
                  <div key={m.id} className="text-sm text-ink-200 font-mono whitespace-pre-wrap leading-relaxed">
                    {m.text}
                  </div>
                ))}
              </div>
            </div>
          ))}
          {Object.keys(stream.messagesByAgent).length === 0 &&
            <div className="text-ink-400 text-sm">No agent messages yet.</div>}
        </div>
      )}

      {tab === "citations" && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {Object.values(stream.citations).map(c => (
            <CitationCard key={c.key} c={c}
              fileUrl={c.pdf_path ? fileUrl(runId, c.pdf_path.replace(/^.*\/workspaces\/[^\/]+\//, "")) : undefined} />
          ))}
          {Object.keys(stream.citations).length === 0 &&
            <div className="text-ink-400 text-sm col-span-full">No citations registered yet.</div>}
        </div>
      )}

      {tab === "pods" && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {Object.values(stream.pods).map(p => <PodCard key={p.pod_id} pod={p} />)}
          {Object.keys(stream.pods).length === 0 &&
            <div className="text-ink-400 text-sm">No pods provisioned yet.</div>}
        </div>
      )}

      {tab === "metrics" && (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-3">
          {Object.entries(stream.metrics).map(([k, pts]) => (
            <MetricChart key={k} experimentId={k} points={pts} />
          ))}
          {Object.keys(stream.metrics).length === 0 &&
            <div className="text-ink-400 text-sm">No metrics streamed yet.</div>}
        </div>
      )}

      {tab === "artifacts" && (
        <div className="grid grid-cols-12 gap-4">
          <div className="col-span-4 border border-ink-700 rounded-lg bg-ink-800 max-h-[70vh] overflow-auto">
            {stream.files.map(f => (
              <button key={f.path}
                onClick={() => setSelectedFile(f.path.replace(/^.*\/workspaces\/[^\/]+\//, ""))}
                className={`block w-full text-left px-3 py-2 text-xs font-mono border-b border-ink-700 hover:bg-ink-700 ${
                  selectedFile && f.path.endsWith(selectedFile) ? "bg-ink-700" : ""}`}>
                <div className="text-ink-100 truncate">{f.path.split("/").pop()}</div>
                <div className="text-ink-400 text-[10px]">{f.kind} · {f.size_bytes}B</div>
              </button>
            ))}
            {stream.files.length === 0 && <div className="text-ink-400 text-sm p-3">No artifacts yet.</div>}
          </div>
          <div className="col-span-8">
            {selectedFile && <ArtifactViewer runId={runId} path={selectedFile} />}
            {!selectedFile && <div className="text-ink-400 text-sm">Select a file to preview.</div>}
          </div>
        </div>
      )}

      {stream.errors.length > 0 && (
        <div className="mt-4 border border-red-500/40 bg-red-500/5 rounded-lg p-3 text-sm">
          <div className="text-red-300 font-mono text-xs mb-2">errors ({stream.errors.length})</div>
          {stream.errors.slice(-5).map((e, i) => (
            <div key={i} className="text-red-200 text-xs font-mono">[{e.where}] {e.message}</div>
          ))}
        </div>
      )}
    </div>
  );
}
