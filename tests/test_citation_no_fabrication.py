"""The no-fabrication invariant — the most important guarantee in this repo.

`CitationManifest.add` MUST refuse to persist any citation that:
  - has neither DOI nor arXiv ID
  - has a malformed DOI/arxiv ID
  - has an unreachable source URL
  - has a title that doesn't match (>=85 fuzz) what Crossref/arXiv reports

And MUST publish a `citation.added` event on the happy path.
"""

import httpx
import pytest
import respx

from auto_research.citations.manifest import (
    Citation,
    CitationManifest,
    NoFabricationError,
)
from auto_research.events.bus import SQLiteEventBus


@pytest.fixture
async def manifest(store):
    bus = SQLiteEventBus(store)
    rid = await store.create_run("p")
    client = httpx.AsyncClient(follow_redirects=True)
    m = CitationManifest(store, bus, rid, http_client=client)
    try:
        yield m, bus, rid
    finally:
        await m.close()


async def test_reject_when_no_doi_and_no_arxiv(manifest):
    m, _, _ = manifest
    bad = Citation(
        key="lewis2020rag", title="Retrieval-Augmented Generation",
        authors=("Lewis et al.",), url="https://example.com/paper.pdf",
    )
    with pytest.raises(NoFabricationError, match="neither a DOI nor an arXiv ID"):
        await m.add(bad)


async def test_reject_when_doi_malformed(manifest):
    m, _, _ = manifest
    bad = Citation(
        key="x", title="t", authors=("a",),
        url="https://example.com", doi="not-a-doi",
    )
    with pytest.raises(NoFabricationError, match="DOI"):
        await m.add(bad)


async def test_reject_when_arxiv_id_malformed(manifest):
    m, _, _ = manifest
    bad = Citation(
        key="x", title="t", authors=("a",),
        url="https://example.com", arxiv_id="abc",
    )
    with pytest.raises(NoFabricationError, match="arXiv ID"):
        await m.add(bad)


async def test_reject_when_title_fuzz_below_threshold(manifest):
    m, _, _ = manifest
    with respx.mock(assert_all_called=False) as mock:
        mock.head("https://example.com/p.pdf").respond(200)
        mock.get("https://api.crossref.org/works/10.1234/abc").respond(
            json={"message": {
                "title": ["A Completely Different Real Paper About Something Else"],
                "issued": {"date-parts": [[2020]]},
                "container-title": ["NeurIPS"],
                "author": [{"given": "Some", "family": "Author"}],
            }},
        )
        bad = Citation(
            key="rag2020",
            title="Retrieval-Augmented Generation for NLP Tasks",
            authors=("Lewis et al.",),
            url="https://example.com/p.pdf",
            doi="10.1234/abc",
        )
        with pytest.raises(NoFabricationError, match="title mismatch"):
            await m.add(bad)


async def test_reject_when_url_unreachable(manifest):
    m, _, _ = manifest
    with respx.mock(assert_all_called=False) as mock:
        mock.head("https://gone.example/p.pdf").respond(404)
        mock.get("https://gone.example/p.pdf").respond(404)
        bad = Citation(
            key="x", title="t", authors=("a",),
            url="https://gone.example/p.pdf", doi="10.1234/abc",
        )
        with pytest.raises(NoFabricationError, match="HTTP 404|unreachable"):
            await m.add(bad)


async def test_happy_path_with_doi_persists_and_emits_event(manifest):
    m, bus, rid = manifest
    with respx.mock(assert_all_called=False) as mock:
        mock.head("https://example.com/rag.pdf").respond(200)
        mock.get("https://api.crossref.org/works/10.1234/rag").respond(
            json={"message": {
                "title": ["Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"],
                "issued": {"date-parts": [[2020]]},
                "container-title": ["NeurIPS"],
                "author": [
                    {"given": "Patrick", "family": "Lewis"},
                    {"given": "Ethan", "family": "Perez"},
                ],
            }},
        )
        cand = Citation(
            key="lewis2020rag",
            title="Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
            authors=("Lewis",),
            url="https://example.com/rag.pdf",
            doi="10.1234/rag",
        )
        out = await m.add(cand)
    assert out.key == "lewis2020rag"
    assert out.year == 2020
    assert out.venue == "NeurIPS"
    assert out.authors[0] == "Patrick Lewis"

    rows = await m.store.events_since(rid, 0)
    types = [r.type for r in rows]
    assert "citation.added" in types
    payload = next(r.payload for r in rows if r.type == "citation.added")
    assert payload["doi"] == "10.1234/rag"
    assert payload["title"].startswith("Retrieval-Augmented Generation")


async def test_happy_path_with_arxiv_persists(manifest):
    m, bus, rid = manifest
    arxiv_atom = """
    <feed><entry>
      <id>http://arxiv.org/abs/2005.11401v4</id>
      <title>Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks</title>
      <published>2020-05-22T17:01:00Z</published>
      <author><name>Patrick Lewis</name></author>
      <author><name>Ethan Perez</name></author>
    </entry></feed>
    """
    with respx.mock(assert_all_called=False) as mock:
        mock.head("https://arxiv.org/pdf/2005.11401").respond(200)
        mock.get("https://export.arxiv.org/api/query?id_list=2005.11401").respond(text=arxiv_atom)
        cand = Citation(
            key="lewis2020rag",
            title="Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
            authors=("Lewis et al.",),
            url="https://arxiv.org/pdf/2005.11401",
            arxiv_id="2005.11401",
        )
        out = await m.add(cand)
    assert out.year == 2020
    assert out.venue == "arXiv"
    # Round-trip via get()
    fetched = await m.get("lewis2020rag")
    assert fetched is not None
    assert fetched.arxiv_id == "2005.11401"


async def test_bibtex_serialization():
    from auto_research.citations.bibtex import to_bibtex
    c = Citation(
        key="lewis2020rag",
        title="Retrieval-Augmented Generation for NLP",
        authors=("Patrick Lewis", "Ethan Perez"),
        url="https://arxiv.org/abs/2005.11401",
        arxiv_id="2005.11401",
        year=2020,
        venue="NeurIPS",
    )
    bib = to_bibtex(c)
    # arxiv-only is @misc; the venue still appears via journal field
    assert bib.startswith("@misc{lewis2020rag,")
    assert "title = {Retrieval-Augmented Generation for NLP}" in bib
    assert "author = Patrick Lewis and Ethan Perez" in bib
    assert "eprint = 2005.11401" in bib
    assert "archivePrefix = arXiv" in bib

    # With a DOI and conference venue, becomes @inproceedings
    c2 = Citation(
        key="x", title="t", authors=("a",), url="https://x", doi="10.1/x",
        year=2020, venue="Proceedings of NeurIPS",
    )
    assert to_bibtex(c2).startswith("@inproceedings{x,")


async def test_has_and_all(manifest):
    m, _, rid = manifest
    assert not await m.has("nothing")
    assert await m.all() == []
    # Insert one via the canonical path
    with respx.mock(assert_all_called=False) as mock:
        mock.head("https://arxiv.org/pdf/2005.11401").respond(200)
        mock.get("https://export.arxiv.org/api/query?id_list=2005.11401").respond(
            text="<feed><entry><id>http://arxiv.org/abs/2005.11401</id>"
                 "<title>Retrieval-Augmented Generation</title>"
                 "<published>2020-01-01</published><author><name>X</name></author>"
                 "</entry></feed>",
        )
        await m.add(Citation(
            key="k1", title="Retrieval-Augmented Generation", authors=("X",),
            url="https://arxiv.org/pdf/2005.11401", arxiv_id="2005.11401",
        ))
    assert await m.has("k1")
    assert len(await m.all()) == 1
