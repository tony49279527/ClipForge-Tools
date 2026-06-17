from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool


CORE_TABLE_ORDER = [
    "jobs",
    "clips",
    "storyboard_frames",
    "frame_image_versions",
    "templates",
    "usage_events",
    "schema_migrations",
    "v3_projects",
    "v3_product_truth",
    "v3_assets",
    "v3_shots",
    "v3_prompt_versions",
    "v3_preflight_checks",
    "v3_takes",
    "v3_generation_submissions",
    "v3_reviews",
    "v3_continuity_states",
    "v3_usage_events",
    "v3_final_assemblies",
    "v3_retake_plans",
    "v3_operation_events",
]


@dataclass(frozen=True)
class TableExport:
    table: str
    file: str
    row_count: int
    sha256: str


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_url_summary(url: str) -> dict[str, str | bool]:
    parsed = urlparse(url)
    return {
        "dialect": parsed.scheme.split("+", 1)[0],
        "host_configured": bool(parsed.hostname),
        "host": parsed.hostname or "",
        "database": Path(parsed.path).name if parsed.path else "",
    }


def redact_url(url: str) -> str:
    parsed = urlparse(url)
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return parsed._replace(netloc=netloc, query="").geturl()


def sqlite_path_from_source(source: str) -> Path:
    parsed = urlparse(source)
    if parsed.scheme == "sqlite":
        path = parsed.path
        if parsed.netloc:
            path = f"/{parsed.netloc}{parsed.path}"
        return Path(path).resolve()
    if parsed.scheme:
        raise ValueError("SQLite source must be a filesystem path or sqlite:/// URL")
    return Path(source).resolve()


def open_sqlite_readonly(source: str) -> sqlite3.Connection:
    path = sqlite_path_from_source(source)
    if not path.exists():
        raise FileNotFoundError(f"SQLite database not found: {path}")
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def postgres_engine(url: str) -> Engine:
    parsed = urlparse(url)
    if parsed.scheme.split("+", 1)[0] not in {"postgresql", "postgres"}:
        raise ValueError("Target database must be PostgreSQL")
    host = parsed.hostname or ""
    if host not in {"127.0.0.1", "localhost", "postgres"}:
        raise ValueError("Refusing non-local PostgreSQL target in migration rehearsal tool")
    return create_engine(url, pool_pre_ping=True, poolclass=NullPool, future=True)


def sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    existing = {row["name"] for row in rows}
    ordered = [table for table in CORE_TABLE_ORDER if table in existing]
    ordered.extend(sorted(existing - set(ordered)))
    return ordered


def encode_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__base64__": base64.b64encode(value).decode("ascii")}
    return value


def decode_value(value: Any) -> Any:
    if isinstance(value, dict) and set(value.keys()) == {"__base64__"}:
        return base64.b64decode(value["__base64__"])
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_manifest(source_dir: Path) -> dict[str, Any]:
    return json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))


def table_file(source_dir: Path, table_name: str) -> Path:
    return source_dir / f"{table_name}.jsonl"


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def sqlite_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    schema: dict[str, Any] = {}
    for table in sqlite_tables(conn):
        columns = [
            {
                "name": row["name"],
                "type": row["type"],
                "nullable": not bool(row["notnull"]),
                "primary_key": bool(row["pk"]),
            }
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        ]
        indexes = [
            {"name": row["name"], "unique": bool(row["unique"])}
            for row in conn.execute(f"PRAGMA index_list({table})").fetchall()
        ]
        schema[table] = {"columns": columns, "indexes": indexes}
    return schema


def postgres_schema(engine: Engine) -> dict[str, Any]:
    with engine.connect() as conn:
        tables = [
            row["table_name"]
            for row in conn.execute(
                text(
                    """
                    SELECT table_name
                    FROM information_schema.tables
                    WHERE table_schema = CURRENT_SCHEMA()
                      AND table_type = 'BASE TABLE'
                    ORDER BY table_name
                    """
                )
            ).mappings()
        ]
        schema: dict[str, Any] = {}
        for table in tables:
            columns = [
                {
                    "name": row["column_name"],
                    "type": row["data_type"],
                    "nullable": row["is_nullable"] == "YES",
                    "primary_key": bool(row["is_primary"]),
                }
                for row in conn.execute(
                    text(
                        """
                        SELECT c.column_name, c.data_type, c.is_nullable,
                               CASE WHEN kcu.column_name IS NULL THEN 0 ELSE 1 END AS is_primary
                        FROM information_schema.columns c
                        LEFT JOIN information_schema.table_constraints tc
                          ON tc.table_schema = c.table_schema
                         AND tc.table_name = c.table_name
                         AND tc.constraint_type = 'PRIMARY KEY'
                        LEFT JOIN information_schema.key_column_usage kcu
                          ON kcu.constraint_schema = tc.constraint_schema
                         AND kcu.constraint_name = tc.constraint_name
                         AND kcu.table_name = c.table_name
                         AND kcu.column_name = c.column_name
                        WHERE c.table_schema = CURRENT_SCHEMA()
                          AND c.table_name = :table
                        ORDER BY c.ordinal_position
                        """
                    ),
                    {"table": table},
                ).mappings()
            ]
            indexes = [
                {"name": row["indexname"], "unique": "UNIQUE INDEX" in row["indexdef"].upper()}
                for row in conn.execute(
                    text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = CURRENT_SCHEMA() AND tablename = :table"),
                    {"table": table},
                ).mappings()
            ]
            schema[table] = {"columns": columns, "indexes": indexes}
        return schema


def normalize_type(type_name: str) -> str:
    value = (type_name or "").lower()
    if "int" in value or value in {"serial", "bigserial"}:
        return "integer"
    if "char" in value or "text" in value:
        return "text"
    if value in {"real", "double precision", "numeric", "decimal"}:
        return "real"
    if "bool" in value:
        return "integer"
    return value
