"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { listRuns } from "@/lib/api";
import { PhaseBadge } from "@/components/PhaseBadge";
import { CostTicker } from "@/components/CostTicker";
import type { Run } from "@/lib/types";

export default function RunsList() {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = () => listRuns().then(r => { if (!cancelled) setRuns(r); })
                                 .catch(e => { if (!cancelled) setErr(String(e)); });
    tick();
    const h = setInterval(tick, 3000);
    return () => { cancelled = true; clearInterval(h); };
  }, []);

  if (err) return (
    <div className="text-red-300">
      Failed to reach API: {err}
      <div className="text-ink-400 text-sm mt-2">
        Is the backend running? Try <code className="bg-ink-800 px-1 rounded">uv run research serve</code>.
      </div>
    </div>
  );
  if (runs === null) return <div className="text-ink-400">loading…</div>;
  if (runs.length === 0) return (
    <div className="text-center py-20 border border-dashed border-ink-700 rounded-lg">
      <div className="text-ink-300 mb-2">No runs yet.</div>
      <Link href="/runs/new" className="text-accent hover:underline">Start your first run →</Link>
    </div>
  );

  return (
    <div>
      <h1 className="text-xl font-semibold mb-4">Runs</h1>
      <div className="border border-ink-700 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-ink-800 text-ink-400 text-xs uppercase tracking-wider">
            <tr>
              <th className="text-left px-4 py-2">ID</th>
              <th className="text-left px-4 py-2">Phase</th>
              <th className="text-left px-4 py-2">Status</th>
              <th className="text-left px-4 py-2">Refine</th>
              <th className="text-right px-4 py-2">Cost</th>
              <th className="text-right px-4 py-2">Started</th>
            </tr>
          </thead>
          <tbody>
            {runs.map(r => (
              <tr key={r.id} className="border-t border-ink-700 hover:bg-ink-800/50">
                <td className="px-4 py-2 font-mono text-xs">
                  <Link href={`/runs/${r.id}`} className="text-accent hover:underline">
                    {r.id.slice(0, 12)}
                  </Link>
                </td>
                <td className="px-4 py-2"><PhaseBadge phase={r.current_phase} /></td>
                <td className="px-4 py-2 text-ink-300">{r.status}</td>
                <td className="px-4 py-2 text-ink-400">{r.refinement_round}</td>
                <td className="px-4 py-2 text-right"><CostTicker usd={r.total_cost_usd} /></td>
                <td className="px-4 py-2 text-right text-ink-400 text-xs">
                  {new Date(r.created_at * 1000).toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
