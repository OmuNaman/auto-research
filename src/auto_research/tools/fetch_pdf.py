"""Download a PDF to the workspace, return content-addressed path + sha256."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass(frozen=True)
class FetchedPDF:
    path: Path
    sha256: str
    size_bytes: int
    content_type: str | None


async def fetch_pdf(
    url: str,
    dest_dir: Path,
    *,
    client: httpx.AsyncClient | None = None,
    max_bytes: int = 50 * 1024 * 1024,
) -> FetchedPDF:
    dest_dir.mkdir(parents=True, exist_ok=True)
    own = client is None
    c = client or httpx.AsyncClient(timeout=60.0, follow_redirects=True)
    h = hashlib.sha256()
    tmp = dest_dir / ".tmp_download"
    total = 0
    ctype: str | None = None
    try:
        async with c.stream("GET", url) as resp:
            resp.raise_for_status()
            ctype = resp.headers.get("content-type")
            with tmp.open("wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"PDF exceeds {max_bytes} bytes")
                    h.update(chunk)
                    f.write(chunk)
        digest = h.hexdigest()
        final = dest_dir / f"{digest}.pdf"
        tmp.rename(final)
        return FetchedPDF(path=final, sha256=digest, size_bytes=total, content_type=ctype)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        if own:
            await c.aclose()
