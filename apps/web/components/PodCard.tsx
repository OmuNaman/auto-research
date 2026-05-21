import clsx from "clsx";
import type { PodState } from "@/lib/eventStream";

const STATUS_COLOR: Record<string, string> = {
  provisioning: "bg-yellow-500",
  ready: "bg-blue-500",
  running: "bg-accent",
  terminated: "bg-ink-500",
  failed: "bg-red-500",
};

export function PodCard({ pod }: { pod: PodState }) {
  return (
    <div className="border border-ink-700 rounded-lg overflow-hidden bg-ink-800">
      <div className="px-4 py-3 border-b border-ink-700 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <span className={clsx("w-2 h-2 rounded-full",
            STATUS_COLOR[pod.status] || "bg-ink-500",
            pod.status === "running" && "animate-pulse",
          )} />
          <div className="font-mono text-sm">{pod.pod_id}</div>
          <div className="text-ink-400 text-xs">{pod.gpu}</div>
        </div>
        <div className="text-ink-400 text-xs">
          {pod.ssh_host && `${pod.ssh_host}:${pod.ssh_port}`}
          {pod.usd_per_hour != null && (
            <span className="ml-3">${pod.usd_per_hour.toFixed(2)}/hr</span>
          )}
        </div>
      </div>
      <pre className="bg-ink-900 text-ink-200 text-xs font-mono px-4 py-2 h-48 overflow-auto">
        {pod.log.length === 0 ? <span className="text-ink-400">waiting for logs…</span> : pod.log.join("\n")}
      </pre>
    </div>
  );
}
