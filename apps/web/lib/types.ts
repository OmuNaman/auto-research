// Mirrors auto_research.events.schemas (Python).

export type Phase =
  | "init" | "literature" | "design" | "provision"
  | "execute" | "analyze" | "write" | "done" | "failed";

export type Verdict = "conclusive" | "debunked" | "inconclusive" | "refine";
export type PodStatus = "provisioning" | "ready" | "running" | "terminated" | "failed";

export interface Run {
  id: string;
  status: string;
  current_phase: Phase;
  refinement_round: number;
  total_cost_usd: number;
  created_at: number;
  updated_at: number;
}

export type Ev =
  | { id: number; type: "phase.transition"; run_id: string; ts: number;
      from_phase: Phase; to_phase: Phase; reason: string; refinement_round: number; }
  | { id: number; type: "llm.message_delta"; run_id: string; ts: number;
      agent: string; role: "assistant"|"user"|"system"; text_delta: string; message_id: string; }
  | { id: number; type: "llm.tool_call"; run_id: string; ts: number;
      agent: string; tool: string; tool_call_id: string; input_json: Record<string,unknown>; }
  | { id: number; type: "tool.result"; run_id: string; ts: number;
      tool_call_id: string; tool: string; ok: boolean;
      output_json: Record<string,unknown> | null; error: string | null; duration_ms: number; }
  | { id: number; type: "citation.added"; run_id: string; ts: number;
      key: string; title: string; authors: string[]; year: number | null;
      doi: string | null; arxiv_id: string | null; url: string; pdf_path: string | null; }
  | { id: number; type: "pod.provisioned"; run_id: string; ts: number;
      pod_id: string; runpod_id: string; gpu: string;
      usd_per_hour: number; ssh_host: string; ssh_port: number; }
  | { id: number; type: "pod.status"; run_id: string; ts: number;
      pod_id: string; status: PodStatus; }
  | { id: number; type: "pod.log"; run_id: string; ts: number;
      pod_id: string; stream: "stdout"|"stderr"; lines: string[]; }
  | { id: number; type: "experiment.metric"; run_id: string; ts: number;
      pod_id: string; experiment_id: string; step: number; metrics: Record<string, number>; }
  | { id: number; type: "file.written"; run_id: string; ts: number;
      path: string; kind: string; sha256: string; size_bytes: number; }
  | { id: number; type: "cost.tick"; run_id: string; ts: number;
      total_usd: number; by_pod: Record<string, number>; }
  | { id: number; type: "analyst.verdict"; run_id: string; ts: number;
      refinement_round: number; verdict: Verdict; rationale: string; next_actions: string[]; }
  | { id: number; type: "error"; run_id: string; ts: number;
      where: string; message: string; traceback: string | null; fatal: boolean; };
