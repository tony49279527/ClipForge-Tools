import sqlite3
from datetime import datetime
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).resolve()
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "clipforge.db"))).resolve()


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
            youtube_account_id TEXT,
            upload_to_youtube INTEGER DEFAULT 0,
            stitch_final_video INTEGER DEFAULT 1,
            reference_image_urls_json TEXT,
            status TEXT NOT NULL,
            current_step TEXT,
            final_video_path TEXT,
            youtube_url TEXT,
            total_tokens INTEGER DEFAULT 0,
            estimated_cost_cny REAL DEFAULT 0,
            workflow_version TEXT DEFAULT '1.0',
            workflow_stage TEXT,
            idea_title TEXT,
            simple_idea TEXT,
            target_audience TEXT,
            language TEXT DEFAULT 'zh',
            style_preference TEXT,
            prompt_total_tokens INTEGER DEFAULT 0,
            prompt_estimated_cost_cny REAL DEFAULT 0,
            image_total_tokens INTEGER DEFAULT 0,
            image_estimated_cost_cny REAL DEFAULT 0,
            video_total_tokens INTEGER DEFAULT 0,
            video_estimated_cost_cny REAL DEFAULT 0,
            publish_total_tokens INTEGER DEFAULT 0,
            publish_estimated_cost_cny REAL DEFAULT 0,
            total_estimated_cost_cny REAL DEFAULT 0,
            error_message TEXT,
            uploaded_images_note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    ensure_column(cur, "jobs", "upload_to_youtube", "INTEGER DEFAULT 0")
    ensure_column(cur, "jobs", "stitch_final_video", "INTEGER DEFAULT 1")
    ensure_column(cur, "jobs", "youtube_account_id", "TEXT")
    ensure_column(cur, "jobs", "reference_image_urls_json", "TEXT")
    ensure_column(cur, "jobs", "uploaded_images_note", "TEXT")
    ensure_column(cur, "jobs", "workflow_version", "TEXT DEFAULT '1.0'")
    ensure_column(cur, "jobs", "workflow_stage", "TEXT")
    ensure_column(cur, "jobs", "idea_title", "TEXT")
    ensure_column(cur, "jobs", "simple_idea", "TEXT")
    ensure_column(cur, "jobs", "target_audience", "TEXT")
    ensure_column(cur, "jobs", "language", "TEXT DEFAULT 'zh'")
    ensure_column(cur, "jobs", "style_preference", "TEXT")
    ensure_column(cur, "jobs", "prompt_total_tokens", "INTEGER DEFAULT 0")
    ensure_column(cur, "jobs", "prompt_estimated_cost_cny", "REAL DEFAULT 0")
    ensure_column(cur, "jobs", "image_total_tokens", "INTEGER DEFAULT 0")
    ensure_column(cur, "jobs", "image_estimated_cost_cny", "REAL DEFAULT 0")
    ensure_column(cur, "jobs", "video_total_tokens", "INTEGER DEFAULT 0")
    ensure_column(cur, "jobs", "video_estimated_cost_cny", "REAL DEFAULT 0")
    ensure_column(cur, "jobs", "publish_total_tokens", "INTEGER DEFAULT 0")
    ensure_column(cur, "jobs", "publish_estimated_cost_cny", "REAL DEFAULT 0")
    ensure_column(cur, "jobs", "total_estimated_cost_cny", "REAL DEFAULT 0")
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
    ensure_column(cur, "clips", "frame_id", "INTEGER")
    ensure_column(cur, "clips", "video_prompt_zh", "TEXT")
    ensure_column(cur, "clips", "video_prompt_en", "TEXT")
    ensure_column(cur, "clips", "video_version", "INTEGER DEFAULT 1")
    ensure_column(cur, "clips", "selected_image_version_id", "INTEGER")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS storyboard_frames (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            clip_index INTEGER NOT NULL,
            scene_role TEXT,
            prompt_zh TEXT,
            prompt_en TEXT,
            prompt_version INTEGER DEFAULT 1,
            image_status TEXT DEFAULT 'queued',
            image_model TEXT,
            image_remote_url TEXT,
            image_local_path TEXT,
            image_tokens INTEGER DEFAULT 0,
            image_estimated_cost_cny REAL DEFAULT 0,
            selected_for_video INTEGER DEFAULT 1,
            user_approved INTEGER DEFAULT 0,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS frame_image_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            frame_id INTEGER NOT NULL,
            version_no INTEGER NOT NULL,
            prompt_zh TEXT,
            prompt_en TEXT,
            image_remote_url TEXT,
            image_local_path TEXT,
            image_status TEXT DEFAULT 'queued',
            image_model TEXT,
            tokens INTEGER DEFAULT 0,
            estimated_cost_cny REAL DEFAULT 0,
            raw_usage_json TEXT,
            is_current INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            FOREIGN KEY(frame_id) REFERENCES storyboard_frames(id)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            stage TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id INTEGER,
            action TEXT NOT NULL,
            model_name TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            estimated_cost_cny REAL DEFAULT 0,
            currency TEXT DEFAULT 'CNY',
            raw_usage_json TEXT,
            status TEXT DEFAULT 'succeeded',
            created_at TEXT NOT NULL,
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        )
        """
    )
    conn.commit()
    conn.close()


def ensure_column(cursor: sqlite3.Cursor, table_name: str, column_name: str, column_definition: str) -> None:
    cursor.execute(f"PRAGMA table_info({table_name})")
    existing_columns = {row[1] for row in cursor.fetchall()}
    if column_name not in existing_columns:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")


def create_job(payload: Dict[str, Any]) -> int:
    now = utc_now()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO jobs (
            project_name, product_name, amazon_url, product_brief, video_mode,
            ratio, clip_duration, clip_count, resolution, youtube_title, youtube_account_id,
            youtube_description, privacy, upload_to_youtube, stitch_final_video,
            reference_image_urls_json, status, current_step, final_video_path,
            youtube_url, total_tokens, estimated_cost_cny, workflow_version, workflow_stage,
            idea_title, simple_idea, target_audience, language, style_preference,
            prompt_total_tokens, prompt_estimated_cost_cny, image_total_tokens,
            image_estimated_cost_cny, video_total_tokens, video_estimated_cost_cny,
            publish_total_tokens, publish_estimated_cost_cny, total_estimated_cost_cny, error_message,
            uploaded_images_note, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            payload.get("youtube_account_id", ""),
            payload.get("youtube_description", ""),
            payload["privacy"],
            payload.get("upload_to_youtube", 0),
            payload.get("stitch_final_video", 1),
            payload.get("reference_image_urls_json", "[]"),
            payload.get("status", "queued"),
            payload.get("current_step", "queued"),
            payload.get("final_video_path"),
            payload.get("youtube_url"),
            payload.get("total_tokens", 0),
            payload.get("estimated_cost_cny", 0),
            payload.get("workflow_version", "1.0"),
            payload.get("workflow_stage"),
            payload.get("idea_title", ""),
            payload.get("simple_idea", ""),
            payload.get("target_audience", ""),
            payload.get("language", "zh"),
            payload.get("style_preference", ""),
            payload.get("prompt_total_tokens", 0),
            payload.get("prompt_estimated_cost_cny", 0),
            payload.get("image_total_tokens", 0),
            payload.get("image_estimated_cost_cny", 0),
            payload.get("video_total_tokens", 0),
            payload.get("video_estimated_cost_cny", 0),
            payload.get("publish_total_tokens", 0),
            payload.get("publish_estimated_cost_cny", 0),
            payload.get("total_estimated_cost_cny", 0),
            payload.get("error_message"),
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


def delete_clips_for_job(job_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM clips WHERE job_id = ?", (job_id,))
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


def get_storyboard_frames(job_id: int) -> List[sqlite3.Row]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM storyboard_frames WHERE job_id = ? ORDER BY clip_index ASC", (job_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_storyboard_frame(frame_id: int) -> Optional[sqlite3.Row]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM storyboard_frames WHERE id = ?", (frame_id,))
    row = cur.fetchone()
    conn.close()
    return row


def swap_storyboard_frame_positions(frame_a_id: int, frame_b_id: int) -> None:
    # Fetch both frames and swap clip_index
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, clip_index FROM storyboard_frames WHERE id IN (?, ?)", (frame_a_id, frame_b_id))
    rows = cur.fetchall()
    if len(rows) != 2:
        conn.close()
        raise ValueError(f"Could not find both frames to swap: {frame_a_id}, {frame_b_id}")
    id_to_idx = {row["id"]: row["clip_index"] for row in rows}
    idx_a, idx_b = id_to_idx[frame_a_id], id_to_idx[frame_b_id]
    now = utc_now()
    cur.execute(
        "UPDATE storyboard_frames SET clip_index = ?, updated_at = ? WHERE id = ?",
        (idx_b, now, frame_a_id),
    )
    cur.execute(
        "UPDATE storyboard_frames SET clip_index = ?, updated_at = ? WHERE id = ?",
        (idx_a, now, frame_b_id),
    )
    conn.commit()
    conn.close()


def create_storyboard_frames(job_id: int, frame_specs: Sequence[Dict[str, Any]]) -> None:
    now = utc_now()
    conn = get_conn()
    cur = conn.cursor()
    cur.executemany(
        """
        INSERT INTO storyboard_frames (
            job_id, clip_index, scene_role, prompt_zh, prompt_en, prompt_version,
            image_status, selected_for_video, user_approved, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                job_id,
                spec["clip_index"],
                spec.get("scene_role", ""),
                spec.get("prompt_zh", ""),
                spec.get("prompt_en", ""),
                spec.get("prompt_version", 1),
                spec.get("image_status", "queued"),
                spec.get("selected_for_video", 1),
                spec.get("user_approved", 0),
                now,
                now,
            )
            for spec in frame_specs
        ],
    )
    conn.commit()
    conn.close()


def delete_storyboard_frames_for_job(job_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM frame_image_versions WHERE frame_id IN (SELECT id FROM storyboard_frames WHERE job_id = ?)",
        (job_id,),
    )
    cur.execute("DELETE FROM storyboard_frames WHERE job_id = ?", (job_id,))
    conn.commit()
    conn.close()


def update_storyboard_frame(frame_id: int, fields: Dict[str, Any]) -> None:
    if not fields:
        return
    payload = dict(fields)
    payload["updated_at"] = utc_now()
    keys = list(payload.keys())
    assignments = ", ".join(f"{key} = ?" for key in keys)
    values = [payload[key] for key in keys] + [frame_id]
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"UPDATE storyboard_frames SET {assignments} WHERE id = ?", values)
    conn.commit()
    conn.close()


def create_frame_image_version(payload: Dict[str, Any]) -> int:
    now = utc_now()
    conn = get_conn()
    cur = conn.cursor()
    if payload.get("is_current", 1):
        cur.execute("UPDATE frame_image_versions SET is_current = 0 WHERE frame_id = ?", (payload["frame_id"],))
    cur.execute(
        """
        INSERT INTO frame_image_versions (
            frame_id, version_no, prompt_zh, prompt_en, image_remote_url, image_local_path,
            image_status, image_model, tokens, estimated_cost_cny, raw_usage_json, is_current, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["frame_id"],
            payload["version_no"],
            payload.get("prompt_zh", ""),
            payload.get("prompt_en", ""),
            payload.get("image_remote_url"),
            payload.get("image_local_path"),
            payload.get("image_status", "ready"),
            payload.get("image_model"),
            payload.get("tokens", 0),
            payload.get("estimated_cost_cny", 0),
            payload.get("raw_usage_json"),
            payload.get("is_current", 1),
            now,
        ),
    )
    version_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(version_id)


def list_frame_image_versions(frame_id: int) -> List[sqlite3.Row]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM frame_image_versions WHERE frame_id = ? ORDER BY version_no DESC, id DESC",
        (frame_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_current_frame_image_version(frame_id: int) -> Optional[sqlite3.Row]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM frame_image_versions WHERE frame_id = ? AND is_current = 1 ORDER BY id DESC LIMIT 1",
        (frame_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_frame_image_version(version_id: int) -> Optional[sqlite3.Row]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM frame_image_versions WHERE id = ?", (version_id,))
    row = cur.fetchone()
    conn.close()
    return row


def update_frame_image_version(version_id: int, fields: Dict[str, Any]) -> None:
    if not fields:
        return
    keys = list(fields.keys())
    assignments = ", ".join(f"{key} = ?" for key in keys)
    values = [fields[key] for key in keys] + [version_id]
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"UPDATE frame_image_versions SET {assignments} WHERE id = ?", values)
    conn.commit()
    conn.close()


def set_current_frame_image_version(frame_id: int, version_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    # Mark all versions for this frame as not current
    cur.execute("UPDATE frame_image_versions SET is_current = 0 WHERE frame_id = ?", (frame_id,))
    # Mark selected version as current
    cur.execute("UPDATE frame_image_versions SET is_current = 1 WHERE id = ?", (version_id,))
    # Update storyboard frame to point to the current version
    cur.execute(
        """
        SELECT version_no, image_remote_url, image_local_path, image_status, image_model, tokens, estimated_cost_cny
        FROM frame_image_versions
        WHERE id = ?
        """,
        (version_id,),
    )
    row = cur.fetchone()
    if row:
        cur.execute(
            """
            UPDATE storyboard_frames
            SET image_remote_url = ?, image_local_path = ?, image_status = ?, image_model = ?, image_tokens = ?, image_estimated_cost_cny = ?, prompt_version = ?
            WHERE id = ?
            """,
            (row["image_remote_url"], row["image_local_path"], row["image_status"], row["image_model"], row["tokens"], row["estimated_cost_cny"], row["version_no"], frame_id),
        )
    conn.commit()
    conn.close()


def create_usage_event(payload: Dict[str, Any]) -> int:
    now = utc_now()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO usage_events (
            job_id, stage, entity_type, entity_id, action, model_name, input_tokens,
            output_tokens, total_tokens, estimated_cost_cny, currency, raw_usage_json,
            status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["job_id"],
            payload["stage"],
            payload["entity_type"],
            payload.get("entity_id"),
            payload["action"],
            payload.get("model_name"),
            payload.get("input_tokens", 0),
            payload.get("output_tokens", 0),
            payload.get("total_tokens", 0),
            payload.get("estimated_cost_cny", 0),
            payload.get("currency", "CNY"),
            payload.get("raw_usage_json"),
            payload.get("status", "succeeded"),
            now,
        ),
    )
    event_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(event_id)


def list_usage_events(job_id: int) -> List[sqlite3.Row]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM usage_events WHERE job_id = ? ORDER BY id DESC", (job_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_usage_totals_by_stage(job_id: int) -> Dict[str, Dict[str, float]]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT stage, COALESCE(SUM(total_tokens), 0) AS total_tokens,
               COALESCE(SUM(estimated_cost_cny), 0) AS estimated_cost_cny
        FROM usage_events
        WHERE job_id = ?
        GROUP BY stage
        """,
        (job_id,),
    )
    rows = cur.fetchall()
    conn.close()
    result: Dict[str, Dict[str, float]] = {}
    for row in rows:
        result[row["stage"]] = {
            "total_tokens": row["total_tokens"],
            "estimated_cost_cny": row["estimated_cost_cny"],
        }
    return result
