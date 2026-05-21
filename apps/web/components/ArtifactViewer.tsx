"use client";
import { useEffect, useState } from "react";
import { fileUrl } from "@/lib/api";

export function ArtifactViewer({ runId, path }: { runId: string; path: string }) {
  const [content, setContent] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    setContent(null); setErr(null);
    fetch(fileUrl(runId, path))
      .then(r => r.ok ? r.text() : Promise.reject(`HTTP ${r.status}`))
      .then(setContent)
      .catch(e => setErr(String(e)));
  }, [runId, path]);

  if (err) return <div className="text-red-300 text-sm">Failed: {err}</div>;
  if (content === null) return <div className="text-ink-400 text-sm">loading…</div>;
  return (
    <pre className="text-xs font-mono bg-ink-900 p-3 rounded border border-ink-700 max-h-[70vh] overflow-auto whitespace-pre-wrap">
      {content}
    </pre>
  );
}
