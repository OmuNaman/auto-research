"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { createRun } from "@/lib/api";

const PRESETS: Record<string, string> = {
  smoke: `id: smoke
title: "Smoke test"
problem_statement: |
  Compare two small sentence-embedding models on a tiny STS-B subset.
  Report Spearman correlation. Smoke test.
domains: ["sts"]
success_criteria:
  - "Both models report a valid Spearman score."
compute_hint:
  gpu: "NVIDIA RTX A5000"
  num_pods: 1
  estimated_minutes: 5
`,
  rag_cross_domain: `id: rag_cross_domain
title: "Which RAG configurations are domain-sensitive vs domain-robust?"
problem_statement: |
  Characterize which RAG configurations (chunking strategy, embedding model,
  retriever, reranker) are domain-sensitive vs domain-robust across healthcare
  and legal domains, and explain why.
domains: ["healthcare", "legal"]
compute_hint:
  gpu: "NVIDIA RTX A5000"
  num_pods: 4
  estimated_minutes: 360
`,
};

export default function NewRun() {
  const router = useRouter();
  const [yaml, setYaml] = useState(PRESETS.smoke);
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function go() {
    setSubmitting(true); setErr(null);
    try {
      const r = await createRun(yaml);
      router.push(`/runs/${r.id}`);
    } catch (e) {
      setErr(String(e));
      setSubmitting(false);
    }
  }

  return (
    <div className="max-w-4xl">
      <h1 className="text-xl font-semibold mb-4">New research run</h1>
      <div className="mb-3 flex items-center gap-3 text-sm">
        <span className="text-ink-400">Preset:</span>
        {Object.keys(PRESETS).map(k => (
          <button key={k} onClick={() => setYaml(PRESETS[k])}
                  className="bg-ink-800 hover:bg-ink-700 border border-ink-700 px-3 py-1 rounded text-ink-200">
            {k}
          </button>
        ))}
      </div>
      <textarea
        value={yaml}
        onChange={e => setYaml(e.target.value)}
        className="w-full h-[60vh] bg-ink-900 border border-ink-700 rounded-lg p-4 font-mono text-sm text-ink-100 focus:outline-none focus:ring-1 focus:ring-accent"
        spellCheck={false}
      />
      {err && <div className="text-red-300 mt-2 text-sm">{err}</div>}
      <div className="mt-4 flex gap-3">
        <button
          onClick={go}
          disabled={submitting}
          className="bg-accent text-ink-900 px-4 py-2 rounded font-medium hover:bg-accent/80 disabled:opacity-50"
        >
          {submitting ? "Launching…" : "Launch"}
        </button>
        <a href="/" className="text-ink-300 hover:text-ink-100 px-4 py-2">Cancel</a>
      </div>
    </div>
  );
}
