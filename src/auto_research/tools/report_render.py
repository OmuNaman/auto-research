"""Render the writer's Markdown + the citation manifest into final artifacts.

Outputs:
  - <workspace>/report/report.md       (markdown with [cite:KEY] resolved to (Author, Year))
  - <workspace>/report/report.tex      (LaTeX skeleton with \\cite{KEY})
  - <workspace>/report/references.bib  (BibTeX from manifest)

Unknown [cite:KEY] tokens are STRIPPED and reported as ErrorEvents.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from auto_research.citations.bibtex import to_bibtex
from auto_research.citations.manifest import Citation, CitationManifest
from auto_research.events.bus import EventBus
from auto_research.events.schemas import ErrorEvent, FileWrittenEvent

CITE_RE = re.compile(r"\[cite:([A-Za-z0-9_:.\-]+)\]")


@dataclass
class RenderedReport:
    title: str
    md_path: Path
    tex_path: Path
    bib_path: Path
    stripped_keys: list[str]


def _author_year(c: Citation) -> str:
    last = "Unknown"
    if c.authors:
        first = c.authors[0]
        last = first.split()[-1] if first.split() else first
    yr = str(c.year) if c.year else "n.d."
    return f"({last}, {yr})"


def _md_to_tex_body(md: str) -> str:
    """Minimal Markdown-to-LaTeX. Good enough for headings, lists, inline citations."""
    tex = md
    tex = re.sub(r"^# (.+)$", r"\\section*{\1}", tex, flags=re.M)
    tex = re.sub(r"^## (.+)$", r"\\section{\1}", tex, flags=re.M)
    tex = re.sub(r"^### (.+)$", r"\\subsection{\1}", tex, flags=re.M)
    tex = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", tex)
    tex = re.sub(r"(?<!\*)\*(.+?)\*(?!\*)", r"\\textit{\1}", tex)
    tex = re.sub(r"`([^`]+)`", r"\\texttt{\1}", tex)
    return tex


async def render_report(
    *,
    title: str,
    markdown_body: str,
    manifest: CitationManifest,
    workspace: Path,
    bus: EventBus,
    run_id: str,
) -> RenderedReport:
    report_dir = workspace / "report"
    report_dir.mkdir(parents=True, exist_ok=True)

    citations = await manifest.all()
    by_key = {c.key: c for c in citations}

    # 1) Markdown: replace [cite:KEY] with (Author, Year); strip unknown.
    stripped: list[str] = []

    def md_sub(m: re.Match[str]) -> str:
        k = m.group(1)
        if k in by_key:
            return _author_year(by_key[k])
        stripped.append(k)
        return ""

    md_resolved = CITE_RE.sub(md_sub, markdown_body)

    # 2) Append References section from manifest order
    refs_md_lines = ["", "## References", ""]
    for c in citations:
        authors = ", ".join(c.authors) if c.authors else "Unknown"
        year = c.year or "n.d."
        venue = f" *{c.venue}*." if c.venue else ""
        link = f" [{c.url}]({c.url})"
        refs_md_lines.append(f"- **{c.key}**: {authors} ({year}). {c.title}.{venue}{link}")
    md_full = f"# {title}\n\n{md_resolved}\n\n" + "\n".join(refs_md_lines) + "\n"

    md_path = report_dir / "report.md"
    md_path.write_text(md_full)
    await _emit_file(bus, run_id, md_path, "report_md")

    # 3) LaTeX skeleton
    tex_body = _md_to_tex_body(markdown_body)
    tex_body = CITE_RE.sub(
        lambda m: f"\\cite{{{m.group(1)}}}" if m.group(1) in by_key else "", tex_body,
    )
    tex = (
        "\\documentclass{article}\n"
        "\\usepackage[utf8]{inputenc}\n"
        "\\usepackage{hyperref}\n"
        f"\\title{{{title}}}\n"
        "\\begin{document}\n\\maketitle\n\n"
        + tex_body
        + "\n\n\\bibliographystyle{plain}\n"
        + "\\bibliography{references}\n"
        + "\\end{document}\n"
    )
    tex_path = report_dir / "report.tex"
    tex_path.write_text(tex)
    await _emit_file(bus, run_id, tex_path, "report_tex")

    # 4) BibTeX
    bib_path = report_dir / "references.bib"
    bib_path.write_text("\n\n".join(to_bibtex(c) for c in citations) + "\n")
    await _emit_file(bus, run_id, bib_path, "bibtex")

    # 5) Errors for stripped keys
    for k in sorted(set(stripped)):
        await bus.publish(run_id, ErrorEvent(
            run_id=run_id, where="report_render",
            message=f"[cite:{k}] referenced but not in manifest — stripped.",
            fatal=False,
        ))

    return RenderedReport(
        title=title, md_path=md_path, tex_path=tex_path,
        bib_path=bib_path, stripped_keys=sorted(set(stripped)),
    )


async def _emit_file(bus: EventBus, run_id: str, path: Path, kind: str) -> None:
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    await bus.publish(run_id, FileWrittenEvent(
        run_id=run_id, path=str(path),
        kind=kind,  # type: ignore[arg-type]
        sha256=sha, size_bytes=len(data),
    ))
