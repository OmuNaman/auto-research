"""Serve per-run artifacts from workspaces/<run_id>/."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse


def build_files_router() -> APIRouter:
    router = APIRouter(prefix="/runs", tags=["files"])

    @router.get("/{run_id}/files/{path:path}")
    async def get_file(run_id: str, path: str, request: Request):
        deps = request.app.state.deps
        ws: Path = deps.settings.workspace_for(run_id).resolve()
        candidate = (ws / path).resolve()
        # Directory traversal guard
        if os.path.commonpath([str(candidate), str(ws)]) != str(ws):
            raise HTTPException(403, "path escapes workspace")
        if not candidate.exists() or not candidate.is_file():
            raise HTTPException(404, "file not found")
        return FileResponse(candidate)

    @router.get("/{run_id}/files")
    async def list_files(run_id: str, request: Request) -> dict:
        deps = request.app.state.deps
        ws: Path = deps.settings.workspace_for(run_id).resolve()
        out: list[dict] = []
        for p in ws.rglob("*"):
            if p.is_file():
                rel = p.relative_to(ws)
                stat = p.stat()
                out.append({"path": str(rel), "size_bytes": stat.st_size,
                            "mtime": stat.st_mtime})
        return {"run_id": run_id, "files": out}

    return router
