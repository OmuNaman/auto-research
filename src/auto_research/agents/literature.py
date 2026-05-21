"""Literature sub-agent definition factory + tool builders."""

from __future__ import annotations

from auto_research.llm.base import SubAgentSpec, ToolDef

LITERATURE_SYSTEM_PROMPT = """\
You are the LITERATURE sub-agent inside an autonomous research pipeline.

Your job: given a research problem statement, find 3-8 highly relevant prior \
works using `search_arxiv` and `search_semantic_scholar`, then register each \
with `add_citation`. Every citation MUST have a real DOI or arXiv ID — \
fabricated citations are rejected by the manifest and will fail your task.

When you are confident you have enough coverage to anchor the rest of the \
research (foundation papers + the closest prior art), emit a final JSON \
summary as the LAST message in this exact form:

{
  "added_citation_keys": ["lewis2020rag", "karpukhin2020dpr", ...],
  "rationale": "<one paragraph: why these papers, what they cover>"
}
"""


def literature_subagent(allowed_tools: list[str]) -> SubAgentSpec:
    return SubAgentSpec(
        name="literature",
        description="Searches arXiv and Semantic Scholar for prior work and "
                    "registers verified citations.",
        prompt=LITERATURE_SYSTEM_PROMPT,
        tools=allowed_tools,
    )


def make_literature_tools(
    *,
    on_search_arxiv,
    on_search_s2,
    on_add_citation,
) -> list[ToolDef]:
    """Build the tool surface the literature sub-agent has access to.

    Callers inject closures that bind to the run_id + citation manifest so the
    tool handlers can side-effect into the EventBus and SQLite.
    """
    return [
        ToolDef(
            name="search_arxiv",
            description="Search arXiv for papers. Returns up to `limit` hits with "
                        "arxiv_id, title, authors, summary, pdf_url.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 30, "default": 10},
                },
                "required": ["query"],
            },
            handler=on_search_arxiv,
        ),
        ToolDef(
            name="search_semantic_scholar",
            description="Search Semantic Scholar. Returns hits with paper_id, doi, "
                        "arxiv_id, title, authors, year, venue, tldr.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 30, "default": 10},
                },
                "required": ["query"],
            },
            handler=on_search_s2,
        ),
        ToolDef(
            name="add_citation",
            description=(
                "Register a citation in the manifest. Rejected unless (DOI or arXiv ID) "
                "is provided, the URL is reachable, and the title matches the canonical "
                "resolver. Use this after a search to record papers you intend to cite."
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
