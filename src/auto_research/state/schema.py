"""SQL DDL for the run-state SQLite database.

WAL mode is enabled by the store on open so multiple readers + one writer work
without locking. The `events.id` column is the monotonic per-database cursor
used as the SSE Last-Event-ID.
"""

DDL_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS runs (
        id              TEXT PRIMARY KEY,
        problem_yaml    TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'pending',
        current_phase   TEXT NOT NULL DEFAULT 'init',
        refinement_round INTEGER NOT NULL DEFAULT 0,
        total_cost_usd  REAL NOT NULL DEFAULT 0.0,
        created_at      REAL NOT NULL,
        updated_at      REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id      TEXT NOT NULL,
        ts          REAL NOT NULL,
        type        TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_run_id_id ON events(run_id, id)",
    """
    CREATE TABLE IF NOT EXISTS pods (
        id              TEXT PRIMARY KEY,
        run_id          TEXT NOT NULL,
        runpod_id       TEXT NOT NULL,
        status          TEXT NOT NULL,
        gpu             TEXT NOT NULL,
        usd_per_hour    REAL NOT NULL,
        started_at      REAL NOT NULL,
        terminated_at   REAL,
        ssh_host        TEXT,
        ssh_port        INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pods_run_id ON pods(run_id)",
    """
    CREATE TABLE IF NOT EXISTS citations (
        key         TEXT PRIMARY KEY,
        run_id      TEXT NOT NULL,
        doi         TEXT,
        arxiv_id    TEXT,
        title       TEXT NOT NULL,
        authors_json TEXT NOT NULL,
        year        INTEGER,
        venue       TEXT,
        url         TEXT NOT NULL,
        pdf_path    TEXT,
        added_at    REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_citations_run_id ON citations(run_id)",
    """
    CREATE TABLE IF NOT EXISTS artifacts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id      TEXT NOT NULL,
        kind        TEXT NOT NULL,
        path        TEXT NOT NULL,
        sha256      TEXT NOT NULL,
        size_bytes  INTEGER NOT NULL,
        created_at  REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_artifacts_run_id ON artifacts(run_id)",
    """
    CREATE TABLE IF NOT EXISTS phase_checkpoints (
        run_id          TEXT NOT NULL,
        phase           TEXT NOT NULL,
        payload_json    TEXT NOT NULL,
        ts              REAL NOT NULL,
        PRIMARY KEY (run_id, phase)
    )
    """,
)
