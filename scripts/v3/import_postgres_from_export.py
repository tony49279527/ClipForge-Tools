from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import text

from database_migration_lib import CORE_TABLE_ORDER, decode_value, iter_jsonl, postgres_engine, read_manifest, redact_url, table_file

CONFIRMATION = "IMPORT_CLIPFORGE_EXPORT_ONCE"


def _safe_identifier(name: str) -> str:
    if not name.replace("_", "").isalnum() or name[0].isdigit():
        raise ValueError(f"Unsafe SQL identifier: {name}")
    return name


def _table_order(manifest: dict) -> list[str]:
    tables = [entry["table"] for entry in manifest["tables"]]
    ordered = [table for table in CORE_TABLE_ORDER if table in tables]
    ordered.extend(sorted(set(tables) - set(ordered)))
    return ordered


def import_export(source_dir: Path, target_database_url: str, *, dry_run: bool, validate_only: bool, confirmation: str | None) -> dict:
    manifest = read_manifest(source_dir)
    engine = postgres_engine(target_database_url)
    summary = {"dry_run": dry_run, "validate_only": validate_only, "tables": []}
    with engine.begin() as conn:
        for table in _table_order(manifest):
            _safe_identifier(table)
            path = table_file(source_dir, table)
            rows = list(iter_jsonl(path))
            existing = conn.execute(text(f"SELECT COUNT(*) AS count FROM {table}")).mappings().one()["count"]
            if table == "schema_migrations" and existing == len(rows):
                summary["tables"].append({"table": table, "rows": len(rows), "existing": existing, "action": "already_present"})
                continue
            if existing:
                raise RuntimeError(f"Refusing to import into non-empty table {table}")
            summary["tables"].append({"table": table, "rows": len(rows), "existing": existing})
            if dry_run or validate_only or not rows:
                continue
            if confirmation != CONFIRMATION:
                raise RuntimeError(f"Write import requires --execute-confirm {CONFIRMATION}")
            columns = list(rows[0].keys())
            for column in columns:
                _safe_identifier(column)
            sql = text(
                f"INSERT INTO {table} ({', '.join(columns)}) "
                f"VALUES ({', '.join(':' + column for column in columns)})"
            )
            conn.execute(sql, [{key: decode_value(value) for key, value in row.items()} for row in rows])
            if any("id" in row for row in rows):
                conn.execute(
                    text(
                        """
                        SELECT setval(
                            pg_get_serial_sequence(:table_name, 'id'),
                            GREATEST(COALESCE((SELECT MAX(id) FROM """ + table + """), 0), 1),
                            true
                        )
                        """
                    ),
                    {"table_name": table},
                )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a ClipForge JSONL SQLite export into a PostgreSQL rehearsal database.")
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--target-database-url", required=True)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--execute-confirm")
    args = parser.parse_args()
    dry_run = args.dry_run and args.execute_confirm != CONFIRMATION
    summary = import_export(
        args.source_dir,
        args.target_database_url,
        dry_run=dry_run,
        validate_only=args.validate_only,
        confirmation=args.execute_confirm,
    )
    print(f"TARGET {redact_url(args.target_database_url)}")
    print(f"DRY_RUN {summary['dry_run']} VALIDATE_ONLY {summary['validate_only']}")
    for table in summary["tables"]:
        print(f"{table['table']}: rows={table['rows']} existing={table['existing']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
