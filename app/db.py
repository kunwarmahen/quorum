"""Tiny SQLite data layer. One row per media file, tracking each pipeline stage.

Stage status values: pending | running | done | error
Overall status:      new | recording | processing | done | error
"""
import os
import sqlite3
import threading
from datetime import datetime, timezone

import config

_LOCK = threading.Lock()

# Columns that callers are allowed to update.
_UPDATABLE = {
    "name", "kind", "status",
    "audio_path", "audio_status",
    "transcript_path", "transcript_text", "transcript_status",
    "minutes_path", "minutes_text", "minutes_level", "minutes_status",
    "error", "updated_at", "is_recording",
    "size_bytes", "duration_seconds",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    os.makedirs(config.RECORDINGS_DIR, exist_ok=True)
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS recordings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                source_path TEXT UNIQUE NOT NULL,
                kind TEXT NOT NULL DEFAULT 'video',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new',
                is_recording INTEGER NOT NULL DEFAULT 0,
                audio_path TEXT,
                audio_status TEXT NOT NULL DEFAULT 'pending',
                transcript_path TEXT,
                transcript_text TEXT,
                transcript_status TEXT NOT NULL DEFAULT 'pending',
                minutes_path TEXT,
                minutes_text TEXT,
                minutes_level TEXT,
                minutes_status TEXT NOT NULL DEFAULT 'pending',
                error TEXT
            )
            """
        )
        # Simple key/value store for app-wide config (e.g. selected models).
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings ("
            "key TEXT PRIMARY KEY, value TEXT)"
        )
        # Columns added after the initial release; add them if this is an
        # existing DB created before they existed.
        have = {r["name"] for r in conn.execute("PRAGMA table_info(recordings)")}
        if "size_bytes" not in have:
            conn.execute("ALTER TABLE recordings ADD COLUMN size_bytes INTEGER")
        if "duration_seconds" not in have:
            conn.execute("ALTER TABLE recordings ADD COLUMN duration_seconds REAL")
        conn.commit()
    # If the app died mid-stage, unstick anything left "running".
    reset_running()


# --- Settings key/value store ---------------------------------------------
def get_setting(key: str, default=None):
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()


def reset_running() -> None:
    with _LOCK, _connect() as conn:
        for col in ("audio_status", "transcript_status", "minutes_status"):
            conn.execute(
                f"UPDATE recordings SET {col}='pending' WHERE {col}='running'"
            )
        conn.execute(
            "UPDATE recordings SET status='error', is_recording=0, "
            "error=COALESCE(error,'Interrupted by restart') "
            "WHERE is_recording=1"
        )
        conn.commit()


def add_file(source_path: str, kind: str, name: str = None,
             is_recording: bool = False) -> int:
    """Register a media file. Returns the row id (existing id if already known)."""
    name = name or os.path.basename(source_path)
    existing = get_by_source(source_path)
    if existing:
        return existing["id"]
    now = _now()
    # Audio files need no conversion: mark that stage done up front.
    audio_path = source_path if kind == "audio" else None
    audio_status = "done" if kind == "audio" else "pending"
    with _LOCK, _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO recordings
                (name, source_path, kind, created_at, updated_at, status,
                 is_recording, audio_path, audio_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name, source_path, kind, now, now,
             "recording" if is_recording else "new",
             1 if is_recording else 0, audio_path, audio_status),
        )
        conn.commit()
        return cur.lastrowid


def update(rec_id: int, **fields) -> None:
    fields = {k: v for k, v in fields.items() if k in _UPDATABLE}
    if not fields:
        return
    fields["updated_at"] = _now()
    cols = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [rec_id]
    with _LOCK, _connect() as conn:
        conn.execute(f"UPDATE recordings SET {cols} WHERE id=?", vals)
        conn.commit()


def get(rec_id: int):
    with _connect() as conn:
        row = conn.execute("SELECT * FROM recordings WHERE id=?", (rec_id,)).fetchone()
        return dict(row) if row else None


def get_by_source(source_path: str):
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM recordings WHERE source_path=?", (source_path,)
        ).fetchone()
        return dict(row) if row else None


def list_all():
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM recordings ORDER BY datetime(created_at) DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def active_recording():
    """The row currently being recorded by OBS, if any."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM recordings WHERE is_recording=1 LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def delete(rec_id: int) -> None:
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM recordings WHERE id=?", (rec_id,))
        conn.commit()
