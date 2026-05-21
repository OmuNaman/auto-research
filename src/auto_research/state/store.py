import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiosqlite

from auto_research.state.schema import DDL_STATEMENTS


@dataclass(frozen=True)
class RunRow:
    id: str
    problem_yaml: str
    status: str
    current_phase: str
    refinement_round: int
    total_cost_usd: float
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class EventRow:
    id: int
    run_id: str
    ts: float
    type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class PodRow:
    id: str
    run_id: str
    runpod_id: str
    status: str
    gpu: str
    usd_per_hour: float
    started_at: float
    terminated_at: float | None
    ssh_host: str | None
    ssh_port: int | None


class StateStore:
    """Async SQLite store with WAL. Single connection, serialized internally by aiosqlite."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        for stmt in DDL_STATEMENTS:
            await self._conn.execute(stmt)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("StateStore not opened; call await store.open() first")
        return self._conn

    # ------------------------------------------------------------------ runs

    async def create_run(self, problem_yaml: str, run_id: str | None = None) -> str:
        rid = run_id or uuid.uuid4().hex
        now = time.time()
        await self.conn.execute(
            "INSERT INTO runs (id, problem_yaml, status, current_phase, created_at, updated_at) "
            "VALUES (?, ?, 'running', 'init', ?, ?)",
            (rid, problem_yaml, now, now),
        )
        await self.conn.commit()
        return rid

    async def get_run(self, run_id: str) -> RunRow | None:
        async with self.conn.execute(
            "SELECT id, problem_yaml, status, current_phase, refinement_round, "
            "total_cost_usd, created_at, updated_at FROM runs WHERE id = ?",
            (run_id,),
        ) as cur:
            row = await cur.fetchone()
        return RunRow(*row) if row else None

    async def list_runs(self, limit: int = 50) -> list[RunRow]:
        async with self.conn.execute(
            "SELECT id, problem_yaml, status, current_phase, refinement_round, "
            "total_cost_usd, created_at, updated_at FROM runs "
            "ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        return [RunRow(*r) for r in rows]

    async def set_phase(self, run_id: str, phase: str) -> None:
        await self.conn.execute(
            "UPDATE runs SET current_phase = ?, updated_at = ? WHERE id = ?",
            (phase, time.time(), run_id),
        )
        await self.conn.commit()

    async def set_status(self, run_id: str, status: str) -> None:
        await self.conn.execute(
            "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
            (status, time.time(), run_id),
        )
        await self.conn.commit()

    async def bump_refinement_round(self, run_id: str) -> int:
        await self.conn.execute(
            "UPDATE runs SET refinement_round = refinement_round + 1, updated_at = ? WHERE id = ?",
            (time.time(), run_id),
        )
        await self.conn.commit()
        async with self.conn.execute(
            "SELECT refinement_round FROM runs WHERE id = ?", (run_id,)
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def add_cost(self, run_id: str, delta_usd: float) -> None:
        await self.conn.execute(
            "UPDATE runs SET total_cost_usd = total_cost_usd + ?, updated_at = ? WHERE id = ?",
            (delta_usd, time.time(), run_id),
        )
        await self.conn.commit()

    # ---------------------------------------------------------------- events

    async def append_event(self, run_id: str, type_: str, payload: dict[str, Any]) -> int:
        ts = payload.get("ts", time.time())
        cursor = await self.conn.execute(
            "INSERT INTO events (run_id, ts, type, payload_json) VALUES (?, ?, ?, ?)",
            (run_id, ts, type_, json.dumps(payload, default=str)),
        )
        await self.conn.commit()
        return int(cursor.lastrowid or 0)

    async def events_since(
        self, run_id: str, after_id: int, limit: int = 1000
    ) -> list[EventRow]:
        async with self.conn.execute(
            "SELECT id, run_id, ts, type, payload_json FROM events "
            "WHERE run_id = ? AND id > ? ORDER BY id ASC LIMIT ?",
            (run_id, after_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [
            EventRow(id=r[0], run_id=r[1], ts=r[2], type=r[3], payload=json.loads(r[4]))
            for r in rows
        ]

    # ------------------------------------------------------------------ pods

    async def upsert_pod(
        self,
        *,
        id: str,
        run_id: str,
        runpod_id: str,
        status: str,
        gpu: str,
        usd_per_hour: float,
        started_at: float,
        terminated_at: float | None = None,
        ssh_host: str | None = None,
        ssh_port: int | None = None,
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO pods (id, run_id, runpod_id, status, gpu, usd_per_hour,
                              started_at, terminated_at, ssh_host, ssh_port)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status = excluded.status,
                terminated_at = excluded.terminated_at,
                ssh_host = excluded.ssh_host,
                ssh_port = excluded.ssh_port
            """,
            (id, run_id, runpod_id, status, gpu, usd_per_hour,
             started_at, terminated_at, ssh_host, ssh_port),
        )
        await self.conn.commit()

    async def pods_for_run(self, run_id: str) -> list[PodRow]:
        async with self.conn.execute(
            "SELECT id, run_id, runpod_id, status, gpu, usd_per_hour, "
            "started_at, terminated_at, ssh_host, ssh_port FROM pods WHERE run_id = ?",
            (run_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [PodRow(*r) for r in rows]

    # ----------------------------------------------------------- checkpoints

    async def save_checkpoint(
        self, run_id: str, phase: str, payload: dict[str, Any]
    ) -> None:
        await self.conn.execute(
            """
            INSERT INTO phase_checkpoints (run_id, phase, payload_json, ts)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id, phase) DO UPDATE SET
                payload_json = excluded.payload_json,
                ts = excluded.ts
            """,
            (run_id, phase, json.dumps(payload, default=str), time.time()),
        )
        await self.conn.commit()

    async def load_checkpoint(self, run_id: str, phase: str) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT payload_json FROM phase_checkpoints WHERE run_id = ? AND phase = ?",
            (run_id, phase),
        ) as cur:
            row = await cur.fetchone()
        return json.loads(row[0]) if row else None
