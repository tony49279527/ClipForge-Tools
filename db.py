from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
import os
from pathlib import Path
import re
import sqlite3 as _sqlite3
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlparse

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError as SQLAlchemyIntegrityError
from sqlalchemy.pool import NullPool

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).resolve()
DATABASE_URL = os.getenv("DATABASE_URL", "")
DB_URL = os.getenv("DB_URL", "")
_ENGINE: Engine | None = None


def _resolve_db_path() -> Path:
    if DATABASE_URL:
        return Path(os.getenv("DB_PATH", str(DATA_DIR / "clipforge.db"))).resolve()
    if DB_URL:
        parsed = urlparse(DB_URL)
        if parsed.scheme and parsed.scheme != "sqlite":
            raise RuntimeError("Only sqlite DB_URL is supported in this build. Use DB_PATH for local development.")
        if parsed.scheme == "sqlite":
            path = parsed.path
            if parsed.netloc:
                path = f"/{parsed.netloc}{parsed.path}"
            return Path(path).resolve()
    return Path(os.getenv("DB_PATH", str(DATA_DIR / "clipforge.db"))).resolve()


DB_PATH = _resolve_db_path()
SQL_IDENTIFIER_ALLOWLIST = {
    "jobs",
    "clips",
    "storyboard_frames",
    "frame_image_versions",
    "templates",
    "usage_events",
}
ALL_TABLE_ALLOWLIST = SQL_IDENTIFIER_ALLOWLIST | {
    "schema_migrations",
    "v3_projects",
    "v3_product_truth",
    "v3_assets",
    "v3_shots",
    "v3_prompt_versions",
    "v3_takes",
    "v3_reviews",
    "v3_continuity_states",
    "v3_usage_events",
    "v3_preflight_checks",
    "v3_final_assemblies",
    "v3_retake_plans",
    "v3_generation_submissions",
    "v3_operation_events",
}


class DatabaseIntegrityError(_sqlite3.IntegrityError):
    """Backend-neutral integrity error raised by the database adapter."""


class DbRow(Mapping[str, Any]):
    def __init__(self, values: Mapping[str, Any] | Sequence[Any], keys: Sequence[str] | None = None):
        if isinstance(values, Mapping):
            self._mapping = dict(values)
            self._keys = list(values.keys())
            self._values = [values[key] for key in self._keys]
            return
        if keys is None:
            raise ValueError("DbRow sequence values require keys")
        self._keys = list(keys)
        self._values = list(values)
        self._mapping = dict(zip(self._keys, self._values))

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._mapping[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def get(self, key: str, default: Any = None) -> Any:
        return self._mapping.get(key, default)


def _database_url() -> str:
    if DATABASE_URL:
        return DATABASE_URL
    if DB_URL:
        parsed = urlparse(DB_URL)
        if parsed.scheme and parsed.scheme != "sqlite":
            raise RuntimeError("DB_URL remains SQLite-only. Use DATABASE_URL for PostgreSQL.")
        return DB_URL
    return f"sqlite:///{DB_PATH}"


def _safe_database_summary(url: str | None = None) -> dict[str, Any]:
    parsed = urlparse(url or _database_url())
    return {
        "dialect": parsed.scheme.split("+", 1)[0] if parsed.scheme else "sqlite",
        "host_configured": bool(parsed.hostname),
        "database": Path(parsed.path).name if parsed.path else "",
    }


def database_dialect() -> str:
    return _safe_database_summary()["dialect"]


def is_sqlite() -> bool:
    return database_dialect() == "sqlite"


def is_postgresql() -> bool:
    return database_dialect() in {"postgresql", "postgres"}


def _sqlite_path_from_url(url: str) -> Path:
    parsed = urlparse(url)
    if parsed.scheme != "sqlite":
        return DB_PATH
    path = parsed.path
    if parsed.netloc:
        path = f"/{parsed.netloc}{parsed.path}"
    return Path(path).resolve()


def get_engine() -> Engine:
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE

    url = _database_url()
    parsed = urlparse(url)
    dialect = parsed.scheme.split("+", 1)[0] if parsed.scheme else "sqlite"
    if dialect == "sqlite":
        sqlite_path = _sqlite_path_from_url(url)
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        timeout = float(os.getenv("SQLITE_TIMEOUT_SECONDS", "30"))
        _ENGINE = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": timeout},
            poolclass=NullPool,
            future=True,
        )

        @event.listens_for(_ENGINE, "connect")
        def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys = ON")
            cursor.execute("PRAGMA journal_mode = WAL")
            cursor.execute("PRAGMA busy_timeout = 30000")
            cursor.close()

        return _ENGINE
    if dialect in {"postgresql", "postgres"}:
        _ENGINE = create_engine(
            url,
            pool_pre_ping=True,
            pool_recycle=int(os.getenv("DATABASE_POOL_RECYCLE_SECONDS", "1800")),
            pool_size=int(os.getenv("DATABASE_POOL_SIZE", "5")),
            max_overflow=int(os.getenv("DATABASE_MAX_OVERFLOW", "5")),
            future=True,
        )
        statement_timeout_ms = os.getenv("DATABASE_STATEMENT_TIMEOUT_MS", "").strip()
        if statement_timeout_ms:
            timeout_ms = int(statement_timeout_ms)

            @event.listens_for(_ENGINE, "connect")
            def _set_postgres_statement_timeout(dbapi_connection: Any, _connection_record: Any) -> None:
                with dbapi_connection.cursor() as cursor:
                    cursor.execute(f"SET statement_timeout = {timeout_ms}")

        return _ENGINE
    raise RuntimeError(f"Unsupported DATABASE_URL dialect: {dialect}")


def reset_engine_for_tests() -> None:
    global _ENGINE
    if _ENGINE is not None:
        _ENGINE.dispose()
    _ENGINE = None


def _convert_qmark_sql(sql: str, params: Sequence[Any] | None) -> tuple[str, dict[str, Any] | Sequence[Any] | None]:
    if params is None:
        return sql, None
    placeholders: list[str] = []
    out: list[str] = []
    in_single = False
    in_double = False
    in_line_comment = False
    in_block_comment = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < len(sql) else ""
        if in_line_comment:
            out.append(ch)
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            out.append(ch)
            if ch == "*" and nxt == "/":
                out.append(nxt)
                in_block_comment = False
                i += 2
            else:
                i += 1
            continue
        if not in_single and not in_double and ch == "-" and nxt == "-":
            out.extend([ch, nxt])
            in_line_comment = True
            i += 2
            continue
        if not in_single and not in_double and ch == "/" and nxt == "*":
            out.extend([ch, nxt])
            in_block_comment = True
            i += 2
            continue
        if ch == "'" and not in_double:
            out.append(ch)
            if in_single and nxt == "'":
                out.append(nxt)
                i += 2
                continue
            in_single = not in_single
            i += 1
            continue
        if ch == '"' and not in_single:
            out.append(ch)
            in_double = not in_double
            i += 1
            continue
        if ch == "?" and not in_single and not in_double:
            name = f"p{len(placeholders)}"
            placeholders.append(name)
            out.append(f":{name}")
            i += 1
            continue
        out.append(ch)
        i += 1
    if len(placeholders) != len(params):
        raise ValueError(f"SQL placeholder count {len(placeholders)} does not match parameter count {len(params)}")
    return "".join(out), {name: params[index] for index, name in enumerate(placeholders)}


def _prepare_sql(sql: str) -> str:
    stripped = sql.strip()
    if is_postgresql():
        sql = re.sub(
            r"\bid\s+INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
            "id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
            sql,
            flags=re.IGNORECASE,
        )
        if stripped.upper().startswith("INSERT OR IGNORE "):
            sql = re.sub(r"^\s*INSERT\s+OR\s+IGNORE\s+", "INSERT ", sql, flags=re.IGNORECASE)
            if " ON CONFLICT" not in sql.upper():
                sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return sql


def _needs_insert_returning_id(sql: str) -> bool:
    if not is_postgresql():
        return False
    normalized = sql.strip().upper()
    if not normalized.startswith("INSERT INTO "):
        return False
    if " RETURNING " in normalized or " ON CONFLICT DO NOTHING" in normalized:
        return False
    return True


def _pragma_table_info(table_name: str) -> list[DbRow]:
    if table_name not in SQL_IDENTIFIER_ALLOWLIST and table_name not in _all_v3_table_names():
        raise ValueError(f"Unsupported table for schema inspection: {table_name}")
    if is_sqlite():
        with get_engine().connect() as conn:
            result = conn.exec_driver_sql(f"PRAGMA table_info({table_name})")
            return [DbRow(row._mapping) for row in result.fetchall()]
    with get_engine().connect() as conn:
        result = conn.execute(
            text(
                """
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = CURRENT_SCHEMA() AND table_name = :table_name
                ORDER BY ordinal_position
                """
            ),
            {"table_name": table_name},
        )
        rows = []
        for index, row in enumerate(result.fetchall()):
            mapping = row._mapping
            rows.append(
                DbRow(
                    {
                        "cid": index,
                        "name": mapping["column_name"],
                        "type": mapping["data_type"],
                        "notnull": 0 if mapping["is_nullable"] == "YES" else 1,
                        "dflt_value": mapping["column_default"],
                        "pk": 1 if mapping["column_name"] == "id" else 0,
                    }
                )
            )
        return rows


def _all_v3_table_names() -> set[str]:
    return ALL_TABLE_ALLOWLIST - SQL_IDENTIFIER_ALLOWLIST


def _validate_identifier(name: str) -> None:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise ValueError(f"Unsafe SQL identifier: {name}")


def _validate_table(table_name: str) -> None:
    if table_name not in ALL_TABLE_ALLOWLIST:
        raise ValueError(f"Unsupported table: {table_name}")


def _validate_columns(columns: Sequence[str]) -> None:
    for column in columns:
        _validate_identifier(column)


class DbCursor:
    def __init__(self, connection: DbConnection):
        self.connection = connection
        self.lastrowid: int | None = None
        self.rowcount: int = -1
        self._rows: list[DbRow] = []
        self._row_index = 0

    def execute(self, sql: str, params: Sequence[Any] | Mapping[str, Any] | None = None) -> DbCursor:
        stripped = sql.strip()
        if stripped.upper().startswith("PRAGMA TABLE_INFO"):
            match = re.search(r"PRAGMA\s+table_info\(([^)]+)\)", stripped, re.IGNORECASE)
            if not match:
                raise ValueError("Unsupported PRAGMA table_info syntax")
            table_name = match.group(1).strip().strip("\"'")
            self._rows = _pragma_table_info(table_name)
            self._row_index = 0
            self.rowcount = len(self._rows)
            return self
        if params is not None and not isinstance(params, Mapping):
            sql, bound_params = _convert_qmark_sql(sql, list(params))
        else:
            bound_params = params
        sql = _prepare_sql(sql)
        returning_id = _needs_insert_returning_id(sql)
        if returning_id:
            sql = sql.rstrip().rstrip(";") + " RETURNING id"
        try:
            result = self.connection._connection.execute(text(sql), bound_params or {})
        except SQLAlchemyIntegrityError as exc:
            raise DatabaseIntegrityError(str(exc.orig)) from exc
        self._rows = []
        self._row_index = 0
        if result.returns_rows:
            fetched = result.fetchall()
            self._rows = [DbRow(row._mapping) for row in fetched]
            if returning_id and self._rows:
                self.lastrowid = int(self._rows[0]["id"])
        elif is_sqlite():
            lastrowid = getattr(result, "lastrowid", None)
            if lastrowid is not None:
                self.lastrowid = int(lastrowid)
        self.rowcount = result.rowcount
        return self

    def executemany(self, sql: str, seq_of_params: Sequence[Sequence[Any] | Mapping[str, Any]]) -> DbCursor:
        for params in seq_of_params:
            self.execute(sql, params)
        return self

    def fetchone(self) -> DbRow | None:
        if self._row_index >= len(self._rows):
            return None
        row = self._rows[self._row_index]
        self._row_index += 1
        return row

    def fetchall(self) -> list[DbRow]:
        rows = self._rows[self._row_index :]
        self._row_index = len(self._rows)
        return rows

    def column_exists(self, table_name: str, column_name: str) -> bool:
        return column_name in {row["name"] for row in _pragma_table_info(table_name)}


class DbConnection:
    def __init__(self, connection: Connection):
        self._connection = connection
        self._transaction = connection.begin()
        self._closed = False

    def cursor(self) -> DbCursor:
        return DbCursor(self)

    def execute(self, sql: str, params: Sequence[Any] | Mapping[str, Any] | None = None) -> DbCursor:
        return self.cursor().execute(sql, params)

    def commit(self) -> None:
        if self._closed:
            return
        self._transaction.commit()
        self._transaction = self._connection.begin()

    def rollback(self) -> None:
        if self._closed:
            return
        self._transaction.rollback()
        self._transaction = self._connection.begin()

    def close(self) -> None:
        if self._closed:
            return
        if self._transaction.is_active:
            self._transaction.rollback()
        self._connection.close()
        self._closed = True

    def __enter__(self) -> DbConnection:
        return self

    def __exit__(self, exc_type: object, _exc: object, _tb: object) -> None:
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()


def utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def get_conn() -> DbConnection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if is_sqlite():
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return DbConnection(get_engine().connect())


connect = get_conn


@contextmanager
def transaction() -> Iterator[DbConnection]:
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def execute(sql: str, params: Sequence[Any] | Mapping[str, Any] | None = None) -> DbCursor:
    conn = get_conn()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur
    finally:
        conn.close()


def fetch_one(sql: str, params: Sequence[Any] | Mapping[str, Any] | None = None) -> DbRow | None:
    conn = get_conn()
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def fetch_all(sql: str, params: Sequence[Any] | Mapping[str, Any] | None = None) -> list[DbRow]:
    conn = get_conn()
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def select_one(sql: str, params: Mapping[str, Any] | None = None) -> DbRow | None:
    return fetch_one(sql, params or {})


def select_all(sql: str, params: Mapping[str, Any] | None = None) -> list[DbRow]:
    return fetch_all(sql, params or {})


def execute_write(sql: str, params: Mapping[str, Any] | None = None) -> int:
    conn = get_conn()
    try:
        cur = conn.execute(sql, params or {})
        rowcount = cur.rowcount
        conn.commit()
        return rowcount
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def insert_row(table_name: str, values: Mapping[str, Any]) -> int:
    _validate_table(table_name)
    _validate_columns(list(values.keys()))
    columns = list(values.keys())
    column_sql = ", ".join(columns)
    value_sql = ", ".join(f":{column}" for column in columns)
    sql = f"INSERT INTO {table_name} ({column_sql}) VALUES ({value_sql})"
    if is_postgresql():
        sql += " RETURNING id"
    conn = get_conn()
    try:
        cur = conn.execute(sql, dict(values))
        row_id = cur.fetchone()["id"] if is_postgresql() else cur.lastrowid
        conn.commit()
        return int(row_id)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_row_by_id(table_name: str, row_id: int, values: Mapping[str, Any]) -> int:
    if not values:
        return 0
    _validate_table(table_name)
    _validate_columns(list(values.keys()))
    assignments = ", ".join(f"{column} = :{column}" for column in values)
    params = dict(values)
    params["id"] = row_id
    return execute_write(f"UPDATE {table_name} SET {assignments} WHERE id = :id", params)


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
        CREATE TABLE IF NOT EXISTS templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            workflow_version TEXT DEFAULT '2.0',
            project_name TEXT,
            product_name TEXT,
            simple_idea TEXT,
            target_audience TEXT,
            video_mode TEXT,
            ratio TEXT,
            clip_count INTEGER,
            clip_duration INTEGER,
            resolution TEXT,
            style_preference TEXT,
            youtube_title TEXT,
            youtube_description TEXT,
            privacy TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
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


def ensure_column(cursor: DbCursor, table_name: str, column_name: str, column_definition: str) -> None:
    if table_name not in SQL_IDENTIFIER_ALLOWLIST:
        raise ValueError(f"Unsupported table for schema update: {table_name}")
    if not cursor.column_exists(table_name, column_name):
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


def get_job_by_id(job_id: int) -> Optional[DbRow]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = cur.fetchone()
    conn.close()
    return row


def get_all_jobs() -> List[DbRow]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM jobs ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_clip_rows_by_job_id(job_id: int) -> List[DbRow]:
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


def sum_clip_metrics(job_id: int) -> DbRow:
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


def get_storyboard_frames(job_id: int) -> List[DbRow]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM storyboard_frames WHERE job_id = ? ORDER BY clip_index ASC", (job_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_storyboard_frame(frame_id: int) -> Optional[DbRow]:
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


def list_frame_image_versions(frame_id: int) -> List[DbRow]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM frame_image_versions WHERE frame_id = ? ORDER BY version_no DESC, id DESC",
        (frame_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_current_frame_image_version(frame_id: int) -> Optional[DbRow]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM frame_image_versions WHERE frame_id = ? AND is_current = 1 ORDER BY id DESC LIMIT 1",
        (frame_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_frame_image_version(version_id: int) -> Optional[DbRow]:
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


def list_usage_events(job_id: int) -> List[DbRow]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM usage_events WHERE job_id = ? ORDER BY id DESC", (job_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


# ── Template Presets ──────────────────────────────────────────────

def create_template(payload: Dict[str, Any]) -> int:
    now = utc_now()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO templates (
            name, workflow_version, project_name, product_name, simple_idea,
            target_audience, video_mode, ratio, clip_count, clip_duration,
            resolution, style_preference, youtube_title, youtube_description,
            privacy, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["name"],
            payload.get("workflow_version", "2.0"),
            payload.get("project_name", ""),
            payload.get("product_name", ""),
            payload.get("simple_idea", ""),
            payload.get("target_audience", ""),
            payload.get("video_mode", ""),
            payload.get("ratio", ""),
            payload.get("clip_count"),
            payload.get("clip_duration"),
            payload.get("resolution", ""),
            payload.get("style_preference", ""),
            payload.get("youtube_title", ""),
            payload.get("youtube_description", ""),
            payload.get("privacy", ""),
            now,
            now,
        ),
    )
    template_id = cur.lastrowid
    conn.commit()
    conn.close()
    return int(template_id)


def list_templates() -> List[DbRow]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM templates ORDER BY updated_at DESC")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_template(template_id: int) -> Optional[DbRow]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM templates WHERE id = ?", (template_id,))
    row = cur.fetchone()
    conn.close()
    return row


def delete_template(template_id: int) -> None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM templates WHERE id = ?", (template_id,))
    conn.commit()
    conn.close()


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
