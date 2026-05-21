"""Semantic Scholar search via the public Graph API.

No API key is strictly required; passing one raises the rate limit. The agent
uses this for citation graph context and BibTeX-ready metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

S2_BASE = "https://api.semanticscholar.org/graph/v1"

_FIELDS = ",".join([
    "paperId", "externalIds", "title", "abstract", "year", "venue",
    "authors", "tldr", "openAccessPdf",
])


@dataclass(frozen=True)
class S2Hit:
    paper_id: str
    doi: str | None
    arxiv_id: str | None
    title: str
    abstract: str | None
    year: int | None
    venue: str | None
    authors: list[str]
    tldr: str | None
    open_pdf_url: str | None


def _parse(item: dict[str, Any]) -> S2Hit:
    ext = item.get("externalIds") or {}
    tldr = (item.get("tldr") or {}).get("text")
    pdf = (item.get("openAccessPdf") or {}).get("url")
    return S2Hit(
        paper_id=item.get("paperId", ""),
        doi=ext.get("DOI"),
        arxiv_id=ext.get("ArXiv"),
        title=(item.get("title") or "").strip(),
        abstract=item.get("abstract"),
        year=item.get("year"),
        venue=item.get("venue"),
        authors=[a.get("name", "") for a in (item.get("authors") or [])],
        tldr=tldr,
        open_pdf_url=pdf,
    )


async def search_semantic_scholar(
    query: str,
    *,
    limit: int = 10,
    api_key: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[S2Hit]:
    own = client is None
    c = client or httpx.AsyncClient(timeout=20.0)
    headers = {"x-api-key": api_key} if api_key else {}
    try:
        r = await c.get(
            f"{S2_BASE}/paper/search",
            params={"query": query, "limit": limit, "fields": _FIELDS},
            headers=headers,
        )
        r.raise_for_status()
        data = r.json().get("data", [])
        return [_parse(item) for item in data]
    finally:
        if own:
            await c.aclose()
