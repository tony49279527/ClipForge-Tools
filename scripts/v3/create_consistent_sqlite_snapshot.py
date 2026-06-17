from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any


CORE_V3_TABLES = [
    "v3_projects",
    "v3_product_truth",
    "v3_assets",
    "v3_shots",
    "v3_prompt_versions",
    "v3_preflight_checks",
    "v3_generation_submissions",
    "v3_takes",
    "v3_usage_events",
    "v3_operation_events",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _connect_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.execute("PRAGMA schema_version").fetchone()
    conn.row_factory = sqlite3.Row
    return conn


def _table_names(conn: sqlite3.Connection) -> list[str]:
    return [
        row["name"]
        for row in conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
    ]


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {table: int(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]) for table in _table_names(conn)}


def _primary_keys(conn: sqlite3.Connection) -> dict[str, list[Any]]:
    keys: dict[str, list[Any]] = {}
    for table in _table_names(conn):
        columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
        if "id" not in columns:
            continue
        keys[table] = [row["id"] for row in conn.execute(f"SELECT id FROM {table} ORDER BY id")]
    return keys


def _null_counts(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    results: dict[str, dict[str, int]] = {}
    for table in _table_names(conn):
        table_counts: dict[str, int] = {}
        columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
        for column in columns:
            table_counts[column] = int(conn.execute(f"SELECT COUNT(*) AS count FROM {table} WHERE {column} IS NULL").fetchone()["count"])
        results[table] = table_counts
    return results


def _status_distribution(conn: sqlite3.Connection, table: str) -> dict[str, int]:
    columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
    if "status" not in columns:
        return {}
    return {
        str(row["status"]): int(row["count"])
        for row in conn.execute(f"SELECT status, COUNT(*) AS count FROM {table} GROUP BY status ORDER BY status")
    }


def build_manifest(snapshot_path: Path) -> dict[str, Any]:
    conn = _connect_readonly(snapshot_path)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        row_counts = _row_counts(conn)
        return {
            "format": "clipforge-sqlite-consistent-snapshot-v1",
            "snapshot": {
                "path": str(snapshot_path),
                "size_bytes": snapshot_path.stat().st_size,
                "sha256": sha256_file(snapshot_path),
                "integrity_check": integrity,
            },
            "tables": row_counts,
            "jobs_status": _status_distribution(conn, "jobs") if "jobs" in row_counts else {},
            "v3_core_counts": {table: row_counts.get(table, 0) for table in CORE_V3_TABLES},
            "primary_keys": _primary_keys(conn),
            "null_counts": _null_counts(conn),
        }
    finally:
        conn.close()


def create_snapshot(*, source: Path, output: Path, manifest_path: Path | None = None, overwrite: bool = False) -> dict[str, Any]:
    source = source.resolve()
    output = output.resolve()
    if source == output:
        raise ValueError("Source and output paths must be different.")
    if not source.exists():
        raise FileNotFoundError(f"SQLite source not found: {source}")
    if output.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing snapshot: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    temp_path: Path | None = None
    try:
        temp_path = output.parent / f"{output.name}.{uuid.uuid4().hex}.tmp"
        src = _connect_readonly(source)
        try:
            dest = sqlite3.connect(temp_path)
            try:
                src.backup(dest)
                dest.execute("PRAGMA journal_mode=DELETE").fetchone()
            finally:
                dest.close()
        finally:
            src.close()
        manifest = build_manifest(temp_path)
        if manifest["snapshot"]["integrity_check"] != "ok":
            raise RuntimeError("Snapshot integrity_check failed.")
        if output.exists():
            output.unlink()
        temp_path.replace(output)
        manifest = build_manifest(output)
        if manifest_path:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(_redact_manifest_path(manifest), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return manifest
    except Exception:
        if temp_path and temp_path.exists():
            temp_path.unlink()
        if output.exists() and output.stat().st_size == 0:
            output.unlink()
        raise


def _redact_manifest_path(manifest: dict[str, Any]) -> dict[str, Any]:
    safe = json.loads(json.dumps(manifest))
    safe["snapshot"]["path"] = Path(safe["snapshot"]["path"]).name
    return safe


def validate_snapshot(*, source: Path, snapshot: Path, manifest_path: Path | None = None) -> dict[str, Any]:
    source_manifest = build_manifest(source.resolve())
    snapshot_manifest = build_manifest(snapshot.resolve())
    checks = {
        "integrity_ok": snapshot_manifest["snapshot"]["integrity_check"] == "ok",
        "row_counts_match": source_manifest["tables"] == snapshot_manifest["tables"],
        "primary_keys_match": source_manifest["primary_keys"] == snapshot_manifest["primary_keys"],
        "null_counts_match": source_manifest["null_counts"] == snapshot_manifest["null_counts"],
        "hash_present": len(snapshot_manifest["snapshot"]["sha256"]) == 64,
    }
    report = {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "snapshot": _redact_manifest_path(snapshot_manifest)}
    if manifest_path:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or validate a consistent ClipForge SQLite snapshot using sqlite3 backup API.")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.validate_only:
        report = validate_snapshot(source=args.source, snapshot=args.output, manifest_path=args.manifest)
        print(report["status"])
        return 0 if report["status"] == "PASS" else 1
    manifest = create_snapshot(source=args.source, output=args.output, manifest_path=args.manifest, overwrite=args.overwrite)
    safe = _redact_manifest_path(manifest)
    print(f"SNAPSHOT_CREATED output={args.output} integrity={safe['snapshot']['integrity_check']} sha256={safe['snapshot']['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
