"""Literature sub-agent definition factory + tool builders."""

from __future__ import annotations

from auto_research.llm.base import SubAgentSpec, ToolDef

# Built-in Claude Code tools enabled for the literature phase.
LITERATURE_BUILTIN_TOOLS = ["WebSearch", "WebFetch"]

LITERATURE_SYSTEM_PROMPT = """\
You are the LITERATURE sub-agent inside an autonomous research pipeline.

You have access to the full internet via WebSearch and WebFetch, plus a
citation registration tool (add_citation). Use them in this workflow:

1. **Search** — use WebSearch with targeted queries: paper titles, author names,
   venue names (NeurIPS, ICML, ICLR, ACL, EMNLP, arXiv), method names.
   Run several searches from different angles to get broad coverage.

2. **Read** — use WebFetch on promising URLs (arXiv abstract pages, journal
   landing pages, conference proceedings) to extract the full metadata:
   exact title, all authors, year, venue, and most importantly the DOI or
   arXiv ID (e.g. "2005.11401" or "10.18653/v1/...").

3. **Register** — call add_citation for each paper you intend to cite. Every
   citation MUST include a real DOI or arXiv ID — fabricated identifiers are
   rejected by the manifest and will fail your task.

4. **Iterate** until you have 3-8 verified citations covering foundation work
   and the closest prior art relevant to the research problem.

When done, emit a final JSON summary as the LAST message in this exact form:

{
  "added_citation_keys": ["lewis2020rag", "karpukhin2020dpr", ...],
  "rationale": "<one paragraph: why these papers, what they cover>"
}
"""


def literature_subagent(allowed_tools: list[str]) -> SubAgentSpec:
    return SubAgentSpec(
        name="literature",
        description="Searches the web for prior work and registers verified citations.",
        prompt=LITERATURE_SYSTEM_PROMPT,
        tools=allowed_tools,
    )


def make_literature_tools(
    *,
    on_add_citation,
) -> list[ToolDef]:
    """Build the MCP tool surface for the literature phase.

    WebSearch and WebFetch are Claude Code built-ins enabled separately via
    builtin_tools; only the citation-gating tool is an MCP tool here.
    """
    return [
        ToolDef(
            name="add_citation",
            description=(
                "Register a citation in the manifest. Rejected unless (DOI or arXiv ID) "
                "is provided, the URL is reachable, and the title matches the canonical "
                "resolver. Call this after reading a paper page to record it."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "BibTeX key, e.g. 'lewis2020rag'"},
                    "title": {"type": "string"},
                    "authors": {"type": "array", "items": {"type": "string"}},
                    "url": {"type": "string", "format": "uri"},
                    "doi": {"type": "string"},
                    "arxiv_id": {"type": "string"},
                    "year": {"type": "integer"},
                    "venue": {"type": "string"},
                },
                "required": ["key", "title", "authors", "url"],
            },
            handler=on_add_citation,
        ),
    ]
