import type { CitationState } from "@/lib/eventStream";
import { ExternalLink } from "lucide-react";

export function CitationCard({ c, fileUrl }: {
  c: CitationState;
  fileUrl?: string;
}) {
  return (
    <div className="border border-ink-700 rounded-lg p-3 bg-ink-800 hover:bg-ink-700/50 transition">
      <div className="flex items-start justify-between gap-2">
        <div className="font-mono text-xs text-accent">{c.key}</div>
        <a href={c.url} target="_blank" rel="noopener" className="text-ink-400 hover:text-ink-100">
          <ExternalLink size={14} />
        </a>
      </div>
      <div className="text-sm font-medium text-ink-100 mt-1 leading-snug">{c.title}</div>
      <div className="text-xs text-ink-400 mt-1">
        {c.authors.slice(0, 3).join(", ")}
        {c.authors.length > 3 && " et al."}
        {c.year && ` · ${c.year}`}
      </div>
      <div className="flex items-center gap-2 mt-2 text-xs">
        {c.doi && <span className="font-mono text-ink-300">doi:{c.doi}</span>}
        {c.arxiv_id && <span className="font-mono text-ink-300">arXiv:{c.arxiv_id}</span>}
        {fileUrl && (
          <a href={fileUrl} target="_blank" rel="noopener" className="ml-auto text-accent hover:underline">
            PDF
          </a>
        )}
      </div>
    </div>
  );
}
