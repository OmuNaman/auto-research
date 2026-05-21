"""BibTeX serialization for a Citation."""

from __future__ import annotations

import re

from auto_research.citations.manifest import Citation

_SAFE = re.compile(r"[^a-zA-Z0-9_:.-]+")


def _escape(s: str) -> str:
    return (
        s.replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("%", r"\%")
        .replace("&", r"\&")
    )


def _entry_type(c: Citation) -> str:
    if c.arxiv_id and not c.doi:
        return "misc"
    if c.venue and any(k in (c.venue or "").lower() for k in ("conf", "proc", "workshop")):
        return "inproceedings"
    return "article"


def to_bibtex(c: Citation) -> str:
    key = _SAFE.sub("_", c.key) or "ref"
    fields: list[tuple[str, str]] = [
        ("title", "{" + _escape(c.title) + "}"),
        ("author", " and ".join(_escape(a) for a in c.authors)),
    ]
    if c.year:
        fields.append(("year", str(c.year)))
    if c.venue:
        venue_field = "booktitle" if _entry_type(c) == "inproceedings" else "journal"
        fields.append((venue_field, "{" + _escape(c.venue) + "}"))
    if c.doi:
        fields.append(("doi", _escape(c.doi)))
    if c.arxiv_id:
        fields.append(("eprint", _escape(c.arxiv_id)))
        fields.append(("archivePrefix", "arXiv"))
    fields.append(("url", _escape(c.url)))

    body = ",\n  ".join(f"{k} = {v}" for k, v in fields)
    return f"@{_entry_type(c)}{{{key},\n  {body}\n}}"
