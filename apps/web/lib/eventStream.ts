"use client";

import { useEffect, useReducer } from "react";
import type { Ev, Phase, PodStatus, Verdict } from "./types";

export interface PodState {
  pod_id: string;
  runpod_id?: string;
  gpu?: string;
  usd_per_hour?: number;
  ssh_host?: string;
  ssh_port?: number;
  status: PodStatus;
  log: string[];
}

export interface CitationState {
  key: string;
  title: string;
  authors: string[];
  year: number | null;
  doi: string | null;
  arxiv_id: string | null;
  url: string;
  pdf_path: string | null;
}

export interface MetricPoint {
  step: number;
  [k: string]: number;
}

export interface FileEntry {
  path: string;
  kind: string;
  sha256: string;
  size_bytes: number;
  ts: number;
}

export interface ToolCallEntry {
  tool_call_id: string;
  agent: string;
  tool: string;
  input: Record<string, unknown>;
  ok?: boolean;
  output?: Record<string, unknown> | null;
  error?: string | null;
  duration_ms?: number;
}

export interface StreamState {
  events: Ev[];
  phases: { phase: Phase; ts: number; reason: string; round: number }[];
  currentPhase: Phase;
  cost: number;
  pods: Record<string, PodState>;
  citations: Record<string, CitationState>;
  metrics: Record<string, MetricPoint[]>;   // by experiment_id
  files: FileEntry[];
  toolCalls: ToolCallEntry[];
  messagesByAgent: Record<string, { id: string; text: string }[]>;
  verdicts: { round: number; verdict: Verdict; rationale: string }[];
  errors: { where: string; message: string; ts: number }[];
  lastEventId: number;
  connected: boolean;
}

const empty: StreamState = {
  events: [],
  phases: [],
  currentPhase: "init",
  cost: 0,
  pods: {},
  citations: {},
  metrics: {},
  files: [],
  toolCalls: [],
  messagesByAgent: {},
  verdicts: [],
  errors: [],
  lastEventId: 0,
  connected: false,
};

type Action =
  | { type: "event"; ev: Ev }
  | { type: "connected"; v: boolean }
  | { type: "reset" };

function reduce(s: StreamState, a: Action): StreamState {
  if (a.type === "reset") return { ...empty };
  if (a.type === "connected") return { ...s, connected: a.v };
  const ev = a.ev;
  const ns: StreamState = {
    ...s,
    events: [...s.events, ev].slice(-5000),
    lastEventId: Math.max(s.lastEventId, ev.id),
  };
  switch (ev.type) {
    case "phase.transition":
      ns.phases = [...s.phases, {
        phase: ev.to_phase, ts: ev.ts, reason: ev.reason,
        round: ev.refinement_round,
      }];
      ns.currentPhase = ev.to_phase;
      return ns;
    case "cost.tick":
      ns.cost = ev.total_usd;
      return ns;
    case "pod.provisioned":
      ns.pods = { ...s.pods, [ev.pod_id]: {
        pod_id: ev.pod_id, runpod_id: ev.runpod_id, gpu: ev.gpu,
        usd_per_hour: ev.usd_per_hour, ssh_host: ev.ssh_host, ssh_port: ev.ssh_port,
        status: "provisioning", log: [],
      }};
      return ns;
    case "pod.status": {
      const cur = s.pods[ev.pod_id] || { pod_id: ev.pod_id, status: "provisioning" as PodStatus, log: [] };
      ns.pods = { ...s.pods, [ev.pod_id]: { ...cur, status: ev.status } };
      return ns;
    }
    case "pod.log": {
      const cur = s.pods[ev.pod_id] || { pod_id: ev.pod_id, status: "running" as PodStatus, log: [] };
      ns.pods = { ...s.pods, [ev.pod_id]: { ...cur, log: [...cur.log, ...ev.lines].slice(-500) } };
      return ns;
    }
    case "citation.added":
      ns.citations = { ...s.citations, [ev.key]: {
        key: ev.key, title: ev.title, authors: ev.authors, year: ev.year,
        doi: ev.doi, arxiv_id: ev.arxiv_id, url: ev.url, pdf_path: ev.pdf_path,
      }};
      return ns;
    case "experiment.metric": {
      const series = s.metrics[ev.experiment_id] || [];
      ns.metrics = { ...s.metrics, [ev.experiment_id]: [...series, { step: ev.step, ...ev.metrics }] };
      return ns;
    }
    case "file.written":
      ns.files = [...s.files, {
        path: ev.path, kind: ev.kind, sha256: ev.sha256,
        size_bytes: ev.size_bytes, ts: ev.ts,
      }];
      return ns;
    case "llm.tool_call":
      ns.toolCalls = [...s.toolCalls, {
        tool_call_id: ev.tool_call_id, agent: ev.agent, tool: ev.tool,
        input: ev.input_json,
      }];
      return ns;
    case "tool.result": {
      ns.toolCalls = s.toolCalls.map(t =>
        t.tool_call_id === ev.tool_call_id
          ? { ...t, ok: ev.ok, output: ev.output_json, error: ev.error,
              duration_ms: ev.duration_ms, tool: t.tool || ev.tool }
          : t,
      );
      return ns;
    }
    case "llm.message_delta": {
      const agent = ev.agent;
      const cur = s.messagesByAgent[agent] || [];
      const last = cur[cur.length - 1];
      if (last && last.id === ev.message_id) {
        const updated = [...cur.slice(0, -1), { id: last.id, text: last.text + ev.text_delta }];
        ns.messagesByAgent = { ...s.messagesByAgent, [agent]: updated.slice(-50) };
      } else {
        ns.messagesByAgent = { ...s.messagesByAgent,
          [agent]: [...cur, { id: ev.message_id, text: ev.text_delta }].slice(-50) };
      }
      return ns;
    }
    case "analyst.verdict":
      ns.verdicts = [...s.verdicts, {
        round: ev.refinement_round, verdict: ev.verdict, rationale: ev.rationale,
      }];
      return ns;
    case "error":
      ns.errors = [...s.errors, { where: ev.where, message: ev.message, ts: ev.ts }];
      return ns;
    default:
      return ns;
  }
}

const STORAGE_KEY = (runId: string) => `run:${runId}:lastId`;

export function useRunStream(runId: string): StreamState {
  const [state, dispatch] = useReducer(reduce, empty);

  useEffect(() => {
    dispatch({ type: "reset" });
    const lastSeen = sessionStorage.getItem(STORAGE_KEY(runId)) ?? "0";
    const url = `/api/runs/${runId}/events?after_id=${lastSeen}`;
    const es = new EventSource(url);
    es.onopen = () => dispatch({ type: "connected", v: true });
    es.onerror = () => dispatch({ type: "connected", v: false });
    es.onmessage = (e) => {
      try {
        const parsed = JSON.parse(e.data);
        dispatch({ type: "event", ev: parsed });
        if (e.lastEventId) sessionStorage.setItem(STORAGE_KEY(runId), e.lastEventId);
      } catch {}
    };
    // SSE events with named types (the default in sse-starlette uses "event:"
    // header, which EventSource exposes via addEventListener per type).
    const types = [
      "phase.transition", "llm.message_delta", "llm.tool_call", "tool.result",
      "citation.added", "pod.provisioned", "pod.status", "pod.log",
      "experiment.metric", "file.written", "cost.tick", "analyst.verdict", "error",
    ];
    const handlers: Array<(e: MessageEvent) => void> = [];
    for (const t of types) {
      const h = (e: MessageEvent) => {
        try {
          const parsed = JSON.parse(e.data);
          dispatch({ type: "event", ev: parsed });
          if (e.lastEventId) sessionStorage.setItem(STORAGE_KEY(runId), e.lastEventId);
        } catch {}
      };
      es.addEventListener(t, h as EventListener);
      handlers.push(h);
    }
    return () => {
      for (const t of types) {
        // best-effort cleanup — closing the source is enough
      }
      es.close();
    };
  }, [runId]);

  return state;
}
