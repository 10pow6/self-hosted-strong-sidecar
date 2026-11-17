"""SQLite storage. One file, no server. Raw Strong data + user annotations."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("STRONG_DB", ROOT / "data" / "strong.db"))
EXTRACTS_DIR = Path(os.environ.get("STRONG_EXTRACTS", ROOT / "strong-extracts"))
PROGRAMS_DIR = Path(os.environ.get("STRONG_PROGRAMS", ROOT / "programs"))

# Review decisions for flagged (workout, exercise) sessions. Keyed by natural key so they
# survive re-imports.
#   ok           : jump is real, use as-is
#   bar_included : session logged INCLUDING the bar (your convention excludes it); subtract bar_weight_lb
#   bar_excluded : session logged WITHOUT the bar (your convention includes it); add bar_weight_lb
#   exclude      : ignore this session in analysis
ANNOTATIONS_DDL = """
CREATE TABLE IF NOT EXISTS annotations (
    started_at    TEXT NOT NULL,
    exercise      TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('ok','bar_included','bar_excluded','exclude')),
    bar_weight_lb REAL,
    note          TEXT,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (started_at, exercise)
)"""

SCHEMA = ANNOTATIONS_DDL + ";\n" + """
CREATE TABLE IF NOT EXISTS imports (
    id              INTEGER PRIMARY KEY,
    filename        TEXT NOT NULL,
    sha256          TEXT NOT NULL UNIQUE,
    imported_at     TEXT NOT NULL DEFAULT (datetime('now')),
    row_count       INTEGER,
    workouts_total  INTEGER,
    workouts_new    INTEGER,
    workouts_changed INTEGER,
    workouts_missing INTEGER
);

-- A workout is identified by its start timestamp (Strong's "Date" column);
-- "Workout #" is kept for reference but can renumber if workouts are deleted.
CREATE TABLE IF NOT EXISTS workouts (
    id              INTEGER PRIMARY KEY,
    started_at      TEXT NOT NULL UNIQUE,
    workout_number  INTEGER,
    name            TEXT,
    duration_sec    INTEGER,
    notes           TEXT,
    content_hash    TEXT NOT NULL,
    first_import_id INTEGER REFERENCES imports(id),
    last_import_id  INTEGER REFERENCES imports(id),
    missing_from_source INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sets (
    id          INTEGER PRIMARY KEY,
    workout_id  INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,          -- row order within the workout
    exercise    TEXT NOT NULL,
    set_order   INTEGER,
    weight_kg   REAL,
    weight_lb   REAL,                      -- as entered in the app (lb), rounded 0.1
    reps        INTEGER,
    rpe         REAL,
    distance_m  REAL,
    seconds     REAL,
    notes       TEXT
);
CREATE INDEX IF NOT EXISTS ix_sets_exercise ON sets(exercise);
CREATE INDEX IF NOT EXISTS ix_sets_workout ON sets(workout_id);

-- "I'm following program X for these exercises since start_date."
-- Program definitions themselves live as JSON files in programs/.
CREATE TABLE IF NOT EXISTS program_assignments (
    id            INTEGER PRIMARY KEY,
    program_slug  TEXT NOT NULL,
    exercise_map  TEXT NOT NULL,          -- JSON {lift: Strong exercise name}
    basis_lb      TEXT,                   -- JSON {lift: lb} (as logged); null = derive from history
    start_date    TEXT NOT NULL,
    end_date      TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- User preferences (JSON values), e.g. bar_tracking, bar_weight_lb.
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Widen the annotations status CHECK for DBs created before 'bar_excluded' existed."""
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='annotations'").fetchone()
    if row and "bar_excluded" not in row[0]:
        with conn:  # single transaction: rolls back entirely on any error
            conn.execute("ALTER TABLE annotations RENAME TO annotations_old")
            conn.execute(ANNOTATIONS_DDL)
            conn.execute("INSERT INTO annotations SELECT * FROM annotations_old")
            conn.execute("DROP TABLE annotations_old")


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = Path(path or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    _migrate(conn)
    conn.executescript(SCHEMA)
    return conn


def bump_version(conn: sqlite3.Connection) -> None:
    """Data version used to invalidate the in-memory analysis cache."""
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('version', '1') "
        "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1"
    )


def data_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key='version'").fetchone()
    return int(row[0]) if row else 0
