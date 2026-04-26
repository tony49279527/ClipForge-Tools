import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "clipforge.db"


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def get_conn() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_name TEXT NOT NULL,
            product_name TEXT NOT NULL,
            amazon_url TEXT,
            product_brief TEXT NOT NULL,
            video_mode TEXT NOT NULL,
            ratio TEXT NOT NULL,
            clip_duration INTEGER NOT NULL,
            clip_count INTEGER NOT NULL,
            resolution TEXT NOT NULL,
            youtube_title TEXT NOT NULL,
            youtube_description TEXT,
            privacy TEXT NOT NULL,
            reference_image_urls_json TEXT,
            status TEXT NOT NULL,
            current_step TEXT,
            final_video_path TEXT,
            youtube_url TEXT,
            total_tokens INTEGER DEFAULT 0,
            estimated_cost_cny REAL DEFAULT 0,
            error_message TEXT,
            upload_to_youtube INTEGER DEFAULT 0,
            final_video INTEGER DEFAULT 1,
            uploaded_images_note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS clips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            clip_index INTEGER NOT NULL,
            prompt TEXT,
            reference_image_url TEXT,
            seedance_task_id TEXT,
            status TEXT NOT NULL,
            local_path TEXT,
            tokens INTEGER DEFAULT 0,
            estimated_cost_cny REAL DEFAULT 0,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        )
        """
    )
    conn.commit()
    conn.close()


def create_job(payload: Dict[str, Any]) -> int:
    now = utc_now()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO jobs (
            project_name, product_name, amazon_url, product_brief, video_mode,
            ratio, clip_duration, clip_count, resolution, youtube_title,
            youtube_description, privacy, reference_image_urls_json, status,
            current_step, final_video_path, youtube_url, total_tokens,
            estimated_cost_cny, error_message, upload_to_youtube, final_video,
            uploaded_images_note, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["project_name"],
            payload["product_name"],
            payload.get("amazon_url", ""),
            payload["product_brief"],
            payload["video_mode"],
            payload["ratio"],
            payload["clip_duration"],
            payload["clip_count"],
            payload["resolution"],
            payload["youtube_title"],
            payload.get("youtube_description", ""),
            payload["privacy"],
            payload.get("reference_image_urls_json", "[]"),
            payload.get("status", "queued"),
            payload.get("current_step", "queued"),
            payload.get("final_video_path"),
            payload.get("youtube_url"),
            payload.get("total_tokens", 0),
            payload.get("estimated_cost_cny", 0),
            payload.get("error_message"),
            payload.get("upload_to_youtube", 0),
            payload.get("final_video", 1),
            payload.get("uploaded_images_note", ""),
            now,
            now,
        ),
    )
    job_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(job_id)


def create_clip_records(job_id: int, clip_count: int) -> None:
    now = utc_now()
    conn = get_conn()
    cur = conn.cursor()
    cur.executemany(
        """
        INSERT INTO clips (
            job_id, clip_index, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        [(job_id, index, "queued", now, now) for index in range(1, clip_count + 1)],
    )
    conn.commit()
    conn.close()


def get_job_by_id(job_id: int) -> Optional[sqlite3.Row]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = cur.fetchone()
    conn.close()
    return row


def get_all_jobs() -> List[sqlite3.Row]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_clip_rows_by_job_id(job_id: int) -> List[sqlite3.Row]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM clips WHERE job_id = ? ORDER BY clip_index ASC", (job_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def update_job_fields(job_id: int, fields: Dict[str, Any]) -> None:
    if not fields:
        return
    payload = dict(fields)
    payload["updated_at"] = utc_now()
    keys = list(payload.keys())
    assignments = ", ".join(f"{key} = ?" for key in keys)
    values = [payload[key] for key in keys] + [job_id]
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", values)
    conn.commit()
    conn.close()


def update_clip_by_job_and_index(job_id: int, clip_index: int, fields: Dict[str, Any]) -> None:
    if not fields:
        return
    payload = dict(fields)
    payload["updated_at"] = utc_now()
    keys = list(payload.keys())
    assignments = ", ".join(f"{key} = ?" for key in keys)
    values = [payload[key] for key in keys] + [job_id, clip_index]
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        f"UPDATE clips SET {assignments} WHERE job_id = ? AND clip_index = ?",
        values,
    )
    conn.commit()
    conn.close()


def sum_clip_metrics(job_id: int) -> sqlite3.Row:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            COALESCE(SUM(tokens), 0) AS total_tokens,
            COALESCE(SUM(estimated_cost_cny), 0) AS estimated_cost_cny
        FROM clips
        WHERE job_id = ?
        """,
        (job_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row
