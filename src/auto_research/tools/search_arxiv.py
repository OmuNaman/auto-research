"""arXiv search — wraps the sync `arxiv` package in asyncio.to_thread."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import arxiv


@dataclass(frozen=True)
class ArxivHit:
    arxiv_id: str          # e.g. "2005.11401"
    title: str
    authors: list[str]
    summary: str
    published_year: int | None
    primary_category: str
    pdf_url: str
    abs_url: str


def _normalize_id(entry_id: str) -> str:
    # entry_id looks like "http://arxiv.org/abs/2005.11401v4"
    tail = entry_id.rsplit("/", 1)[-1]
    return tail.split("v")[0] if "v" in tail else tail


def _search_sync(query: str, limit: int) -> list[ArxivHit]:
    search = arxiv.Search(
        query=query,
        max_results=limit,
        sort_by=arxiv.SortCriterion.Relevance,
    )
    out: list[ArxivHit] = []
    for r in search.results():
        out.append(ArxivHit(
            arxiv_id=_normalize_id(r.entry_id),
            title=r.title.strip().replace("\n", " "),
            authors=[a.name for a in r.authors],
            summary=r.summary.strip().replace("\n", " "),
            published_year=r.published.year if r.published else None,
            primary_category=r.primary_category,
            pdf_url=r.pdf_url,
            abs_url=r.entry_id,
        ))
    return out


async def search_arxiv(query: str, *, limit: int = 10) -> list[ArxivHit]:
    return await asyncio.to_thread(_search_sync, query, limit)
