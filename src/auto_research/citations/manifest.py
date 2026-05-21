"""Citation manifest — the no-fabrication invariant lives here.

`CitationManifest.add(candidate)` is the **only** writer of citations. It refuses
to persist a citation unless:
  1. it has at least one of {DOI, arXiv ID},
  2. a HEAD request on the source URL returns < 400, and
  3. the title returned by the canonical resolver (Crossref for DOI, arXiv API
     for arXiv ID) fuzzy-matches the agent-supplied title at >= 0.85.

`report_render` is expected to strip any `[cite:KEY]` token whose KEY is not
present in the manifest (and emit an `error` event in that case).
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from rapidfuzz import fuzz

from auto_research.events.bus import EventBus
from auto_research.events.schemas import CitationAddedEvent
from auto_research.logging import get_logger
from auto_research.state.store import StateStore

_log = get_logger(__name__)

TITLE_FUZZ_THRESHOLD: int = 85  # rapidfuzz returns 0..100

ARXIV_ID_RE = re.compile(r"^(\d{4}\.\d{4,5})(v\d+)?$|^[a-z\-]+(\.[A-Z]{2})?/\d{7}$", re.I)
DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.I)


class NoFabricationError(ValueError):
    """Raised when a candidate citation fails the verification gate."""


@dataclass(frozen=True)
class Citation:
    key: str
    title: str
    authors: tuple[str, ...]
    url: str
    doi: str | None = None
    arxiv_id: str | None = None
    year: int | None = None
    venue: str | None = None
    pdf_path: Path | None = None
    extras: dict[str, Any] = field(default_factory=dict)


class Resolver:
    """Looks up canonical metadata for a DOI or arXiv ID. Async, httpx-based."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client

    async def by_doi(self, doi: str) -> dict[str, Any] | None:
        url = f"https://api.crossref.org/works/{doi}"
        try:
            r = await self.client.get(url, timeout=15.0)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            _log.warning("citations.crossref_failed", doi=doi, error=str(exc))
            return None
        data = r.json().get("message", {})
        titles = data.get("title") or []
        return {
            "title": titles[0] if titles else "",
            "year": (data.get("issued", {}).get("date-parts") or [[None]])[0][0],
            "venue": data.get("container-title", [None])[0],
            "authors": [
                f"{a.get('given','').strip()} {a.get('family','').strip()}".strip()
                for a in data.get("author", [])
            ],
        }

    async def by_arxiv(self, arxiv_id: str) -> dict[str, Any] | None:
        url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}"
        try:
            r = await self.client.get(url, timeout=15.0)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            _log.warning("citations.arxiv_failed", arxiv_id=arxiv_id, error=str(exc))
            return None
        text = r.text
        # Lightweight Atom parsing — full XML is overkill for title/year/authors
        m_title = re.search(r"<entry>.*?<title>(.+?)</title>", text, re.S)
        m_year = re.search(r"<published>(\d{4})", text)
        authors = re.findall(r"<author>\s*<name>(.+?)</name>", text)
        return {
            "title": (m_title.group(1).strip() if m_title else ""),
            "year": int(m_year.group(1)) if m_year else None,
            "venue": "arXiv",
            "authors": authors,
        }


class CitationManifest:
    def __init__(
        self,
        store: StateStore,
        bus: EventBus,
        run_id: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        pdf_dir: Path | None = None,
    ) -> None:
        self.store = store
        self.bus = bus
        self.run_id = run_id
        self._own_client = http_client is None
        self.client = http_client or httpx.AsyncClient(follow_redirects=True)
        self.resolver = Resolver(self.client)
        self.pdf_dir = pdf_dir

    async def close(self) -> None:
        if self._own_client:
            await self.client.aclose()

    # --------------------------------------------------------------------- add

    async def add(self, candidate: Citation) -> Citation:
        self._validate_ids(candidate)
        await self._verify_url(candidate.url)
        resolved = await self._resolve_and_check_title(candidate)
        await self._persist(resolved)
        await self.bus.publish(self.run_id, CitationAddedEvent(
            run_id=self.run_id,
            key=resolved.key,
            title=resolved.title,
            authors=list(resolved.authors),
            year=resolved.year,
            doi=resolved.doi,
            arxiv_id=resolved.arxiv_id,
            url=resolved.url,
            pdf_path=str(resolved.pdf_path) if resolved.pdf_path else None,
        ))
        return resolved

    def _validate_ids(self, c: Citation) -> None:
        if not c.doi and not c.arxiv_id:
            raise NoFabricationError(
                f"Citation '{c.key}' has neither a DOI nor an arXiv ID — refused."
            )
        if c.doi and not DOI_RE.match(c.doi):
            raise NoFabricationError(f"Citation '{c.key}' DOI '{c.doi}' is malformed.")
        if c.arxiv_id and not ARXIV_ID_RE.match(c.arxiv_id):
            raise NoFabricationError(
                f"Citation '{c.key}' arXiv ID '{c.arxiv_id}' is malformed."
            )

    async def _verify_url(self, url: str) -> None:
        try:
            r = await self.client.head(url, timeout=15.0)
            # Some servers reject HEAD; fall back to a tiny GET
            if r.status_code in {405, 501}:
                r = await self.client.get(url, timeout=15.0,
                                          headers={"Range": "bytes=0-0"})
            if r.status_code >= 400:
                raise NoFabricationError(
                    f"URL {url} returned HTTP {r.status_code}; cannot verify."
                )
        except httpx.HTTPError as exc:
            raise NoFabricationError(f"URL {url} unreachable: {exc}") from exc

    async def _resolve_and_check_title(self, c: Citation) -> Citation:
        resolved: dict[str, Any] | None = None
        if c.doi:
            resolved = await self.resolver.by_doi(c.doi)
        if not resolved and c.arxiv_id:
            resolved = await self.resolver.by_arxiv(c.arxiv_id)
        if not resolved or not resolved.get("title"):
            raise NoFabricationError(
                f"Citation '{c.key}' could not be resolved against Crossref/arXiv."
            )
        score = fuzz.token_set_ratio(c.title.lower(), str(resolved["title"]).lower())
        if score < TITLE_FUZZ_THRESHOLD:
            raise NoFabricationError(
                f"Citation '{c.key}' title mismatch (fuzz={score}): "
                f"agent='{c.title}' canonical='{resolved['title']}'"
            )
        return Citation(
            key=c.key,
            title=str(resolved["title"]),
            authors=tuple(resolved.get("authors") or c.authors),
            url=c.url,
            doi=c.doi,
            arxiv_id=c.arxiv_id,
            year=resolved.get("year") or c.year,
            venue=resolved.get("venue") or c.venue,
            pdf_path=c.pdf_path,
            extras=c.extras,
        )

    async def _persist(self, c: Citation) -> None:
        await self.store.conn.execute(
            """
            INSERT INTO citations
              (key, run_id, doi, arxiv_id, title, authors_json, year, venue, url, pdf_path, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              title = excluded.title, authors_json = excluded.authors_json,
              year = excluded.year, venue = excluded.venue, url = excluded.url,
              pdf_path = excluded.pdf_path
            """,
            (c.key, self.run_id, c.doi, c.arxiv_id, c.title,
             json.dumps(list(c.authors)), c.year, c.venue, c.url,
             str(c.pdf_path) if c.pdf_path else None, time.time()),
        )
        await self.store.conn.commit()

    # ----------------------------------------------------------------- accessors

    async def get(self, key: str) -> Citation | None:
        async with self.store.conn.execute(
            "SELECT key, doi, arxiv_id, title, authors_json, year, venue, url, pdf_path "
            "FROM citations WHERE key = ? AND run_id = ?",
            (key, self.run_id),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        return Citation(
            key=row[0], doi=row[1], arxiv_id=row[2], title=row[3],
            authors=tuple(json.loads(row[4])), year=row[5], venue=row[6],
            url=row[7], pdf_path=Path(row[8]) if row[8] else None,
        )

    async def all(self) -> list[Citation]:
        async with self.store.conn.execute(
            "SELECT key, doi, arxiv_id, title, authors_json, year, venue, url, pdf_path "
            "FROM citations WHERE run_id = ? ORDER BY added_at ASC",
            (self.run_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            Citation(
                key=r[0], doi=r[1], arxiv_id=r[2], title=r[3],
                authors=tuple(json.loads(r[4])), year=r[5], venue=r[6],
                url=r[7], pdf_path=Path(r[8]) if r[8] else None,
            )
            for r in rows
        ]

    async def has(self, key: str) -> bool:
        return (await self.get(key)) is not None
