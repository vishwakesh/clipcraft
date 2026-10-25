from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    settings = get_settings()
    conn = sqlite3.connect(str(settings.db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def _session():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with _session() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                source_url TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                stage TEXT NOT NULL DEFAULT 'queued',
                progress INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                error TEXT,
                video_path TEXT,
                video_title TEXT,
                video_duration REAL,
                transcript_json TEXT,
                language TEXT,
                top_n INTEGER NOT NULL DEFAULT 5
            );
            CREATE TABLE IF NOT EXISTS clips (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                clip_index INTEGER NOT NULL,
                start_seconds REAL NOT NULL,
                end_seconds REAL NOT NULL,
                title TEXT NOT NULL,
                hook TEXT NOT NULL,
                score REAL NOT NULL,
                reason TEXT NOT NULL,
                output_path TEXT,
                render_status TEXT NOT NULL DEFAULT 'queued',
                settings_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_clips_job_id ON clips(job_id);
            """
        )


def create_job(job: dict[str, Any]) -> None:
    now = utc_now()
    with _session() as conn:
        conn.execute(
            "INSERT INTO jobs (id, source_url, status, stage, progress, created_at, updated_at, language, top_n) VALUES (?, ?, 'queued', 'queued', 0, ?, ?, ?, ?)",
            (job["id"], job["source_url"], now, now, job.get("language"), job.get("top_n", 5)),
        )


def update_job(job_id: str, **fields: Any) -> None:
    allowed = {"status", "stage", "progress", "error", "video_path", "video_title", "video_duration", "transcript_json", "language", "top_n"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    updates["updated_at"] = utc_now()
    if "transcript_json" in updates and not isinstance(updates["transcript_json"], str):
        updates["transcript_json"] = json.dumps(updates["transcript_json"], ensure_ascii=False)
    assignments = ", ".join(f"{key} = ?" for key in updates)
    with _session() as conn:
        conn.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", (*updates.values(), job_id))


def get_job(job_id: str) -> dict[str, Any] | None:
    with _session() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def create_clips(job_id: str, clips: list[dict[str, Any]]) -> None:
    now = utc_now()
    with _session() as conn:
        for index, clip in enumerate(clips, start=1):
            conn.execute(
                """INSERT INTO clips
                (id, job_id, clip_index, start_seconds, end_seconds, title, hook, score, reason, output_path, render_status, settings_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'queued', ?, ?, ?)""",
                (clip["id"], job_id, index, clip["start"], clip["end"], clip["title"], clip["hook"], clip["score"], clip["reason"], json.dumps(clip.get("settings", {})), now, now),
            )


def get_clips(job_id: str) -> list[dict[str, Any]]:
    with _session() as conn:
        rows = conn.execute("SELECT * FROM clips WHERE job_id = ? ORDER BY clip_index", (job_id,)).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["settings"] = json.loads(item.pop("settings_json") or "{}")
        result.append(item)
    return result


def get_clip(clip_id: str) -> dict[str, Any] | None:
    with _session() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["settings"] = json.loads(item.pop("settings_json") or "{}")
    return item


def update_clip(clip_id: str, **fields: Any) -> None:
    allowed = {"start_seconds", "end_seconds", "output_path", "render_status", "settings_json", "title", "hook", "score", "reason"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    if "settings_json" in updates and not isinstance(updates["settings_json"], str):
        updates["settings_json"] = json.dumps(updates["settings_json"])
    updates["updated_at"] = utc_now()
    assignments = ", ".join(f"{key} = ?" for key in updates)
    with _session() as conn:
        conn.execute(f"UPDATE clips SET {assignments} WHERE id = ?", (*updates.values(), clip_id))
