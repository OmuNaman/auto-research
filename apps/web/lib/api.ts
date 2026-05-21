import type { Run } from "./types";

const ROOT = "/api";

export async function listRuns(): Promise<Run[]> {
  const r = await fetch(`${ROOT}/runs`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function getRun(id: string): Promise<Run> {
  const r = await fetch(`${ROOT}/runs/${id}`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function createRun(problemYaml: string): Promise<Run> {
  const r = await fetch(`${ROOT}/runs`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ problem_yaml: problemYaml }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function cancelRun(id: string): Promise<void> {
  await fetch(`${ROOT}/runs/${id}/cancel`, { method: "POST" });
}

export async function listFiles(id: string): Promise<{ files: { path: string; size_bytes: number; mtime: number }[] }> {
  const r = await fetch(`${ROOT}/runs/${id}/files`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function fileUrl(id: string, path: string): string {
  return `${ROOT}/runs/${id}/files/${encodeURI(path)}`;
}
