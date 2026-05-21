"use client";
import { LineChart, Line, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer, Legend } from "recharts";
import type { MetricPoint } from "@/lib/eventStream";

const PALETTE = ["#00d4a4", "#60a5fa", "#f472b6", "#fbbf24", "#a78bfa", "#fb7185"];

export function MetricChart({ experimentId, points }: { experimentId: string; points: MetricPoint[] }) {
  const keys = Array.from(new Set(points.flatMap(p => Object.keys(p).filter(k => k !== "step"))));
  return (
    <div className="border border-ink-700 rounded-lg bg-ink-800 p-3">
      <div className="text-sm text-ink-200 mb-2 font-mono">{experimentId}</div>
      <div className="h-64">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={points}>
            <CartesianGrid stroke="#2a2a2a" strokeDasharray="3 3" />
            <XAxis dataKey="step" stroke="#a0a0a0" />
            <YAxis stroke="#a0a0a0" />
            <Tooltip contentStyle={{ background: "#161616", border: "1px solid #2a2a2a" }} />
            <Legend />
            {keys.map((k, i) => (
              <Line key={k} type="monotone" dataKey={k} stroke={PALETTE[i % PALETTE.length]}
                    dot={false} strokeWidth={2} isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
