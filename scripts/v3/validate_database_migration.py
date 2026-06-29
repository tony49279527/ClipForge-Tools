from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sqlalchemy import text

from database_migration_lib import iter_jsonl, postgres_engine, read_manifest, table_file


KEY_TABLES = {
    "jobs": ["id", "status"],
    "v3_projects": ["id", "project_status", "current_stage"],
    "v3_shots": ["id", "project_id", "status"],
    "v3_generation_submissions": ["id", "idempotency_key", "submission_status", "take_id"],
    "v3_takes": ["id", "shot_id", "generation_submission_id", "status"],
    "v3_usage_events": ["id", "event_key", "source_type", "source_id"],
}


def _hash_row(row: dict, fields: list[str]) -> str:
    payload = {field: row.get(field) for field in fields}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def validate_migration(source_dir: Path, postgres_url: str) -> dict:
    manifest = read_manifest(source_dir)
    engine = postgres_engine(postgres_url)
    results: list[dict] = []
    with engine.connect() as conn:
        for table_entry in manifest["tables"]:
            table = table_entry["table"]
            exported_rows = list(iter_jsonl(table_file(source_dir, table)))
            pg_count = conn.execute(text(f"SELECT COUNT(*) AS count FROM {table}")).mappings().one()["count"]
            if pg_count != table_entry["row_count"]:
                results.append({"level": "FAIL", "table": table, "issue": "row_count_mismatch", "export": table_entry["row_count"], "postgres": pg_count})
                continue
            results.append({"level": "PASS", "table": table, "issue": "row_count"})
            if table in KEY_TABLES and exported_rows:
                fields = KEY_TABLES[table]
                exported_hashes = {_hash_row(row, fields) for row in exported_rows}
                rows = conn.execute(text(f"SELECT {', '.join(fields)} FROM {table}")).mappings().all()
                postgres_hashes = {_hash_row(dict(row), fields) for row in rows}
                if exported_hashes != postgres_hashes:
                    results.append({"level": "FAIL", "table": table, "issue": "key_field_hash_mismatch"})
                else:
                    results.append({"level": "PASS", "table": table, "issue": "key_field_hash"})
        for table, column in (
            ("v3_generation_submissions", "idempotency_key"),
            ("v3_usage_events", "event_key"),
            ("v3_takes", "generation_submission_id"),
        ):
            duplicate = conn.execute(
                text(
                    f"""
                    SELECT {column}, COUNT(*) AS count
                    FROM {table}
                    WHERE {column} IS NOT NULL
                    GROUP BY {column}
                    HAVING COUNT(*) > 1
                    LIMIT 1
                    """
                )
            ).mappings().first()
            results.append({"level": "FAIL" if duplicate else "PASS", "table": table, "issue": f"duplicate_{column}"})
        orphan_takes = conn.execute(
            text(
                """
                SELECT COUNT(*) AS count
                FROM v3_generation_submissions s
                LEFT JOIN v3_takes t ON t.id = s.take_id
                WHERE s.take_id IS NOT NULL AND t.id IS NULL
                """
            )
        ).mappings().one()["count"]
        results.append({"level": "FAIL" if orphan_takes else "PASS", "table": "v3_generation_submissions", "issue": "submission_take_orphans", "count": orphan_takes})
    status = "FAIL" if any(result["level"] == "FAIL" for result in results) else "PASS"
    return {"status": status, "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a ClipForge SQLite export after PostgreSQL import without printing sensitive row content.")
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--postgres-url", required=True)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    report = validate_migration(args.source_dir, args.postgres_url)
    if args.json_output:
        args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(report["status"])
    for result in report["results"]:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
