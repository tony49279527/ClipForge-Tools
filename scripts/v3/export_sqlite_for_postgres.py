from __future__ import annotations

import argparse
import json
from pathlib import Path

from database_migration_lib import TableExport, encode_value, open_sqlite_readonly, sha256_file, sqlite_tables, utc_now, write_json


def export_sqlite(source: str, output_dir: Path) -> dict:
    if output_dir.exists():
        raise FileExistsError(f"Refusing to overwrite existing export directory: {output_dir}")
    output_dir.mkdir(parents=True)
    conn = open_sqlite_readonly(source)
    try:
        exports: list[TableExport] = []
        for table in sqlite_tables(conn):
            out_path = output_dir / f"{table}.jsonl"
            row_count = 0
            with out_path.open("w", encoding="utf-8") as fh:
                for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid ASC"):
                    payload = {key: encode_value(row[key]) for key in row.keys()}
                    fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
                    row_count += 1
            exports.append(TableExport(table=table, file=out_path.name, row_count=row_count, sha256=sha256_file(out_path)))
        manifest = {
            "format": "clipforge-sqlite-jsonl-v1",
            "exported_at": utc_now(),
            "source": "sqlite",
            "schema_version": "legacy-plus-v3-current",
            "tables": [export.__dict__ for export in exports],
        }
        write_json(output_dir / "manifest.json", manifest)
        return manifest
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Export a ClipForge SQLite database to neutral JSONL files for PostgreSQL rehearsal.")
    parser.add_argument("--source-sqlite", required=True, help="SQLite database path or sqlite:/// URL")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    manifest = export_sqlite(args.source_sqlite, args.output_dir)
    print(f"EXPORT COMPLETE tables={len(manifest['tables'])} output_dir={args.output_dir}")
    for table in manifest["tables"]:
        print(f"{table['table']}: rows={table['row_count']} sha256={table['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
