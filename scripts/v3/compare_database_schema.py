from __future__ import annotations

import argparse
import json
from pathlib import Path

from database_migration_lib import normalize_type, open_sqlite_readonly, postgres_engine, postgres_schema, redact_url, sqlite_schema


def compare_schema(sqlite_source: str, postgres_url: str) -> dict:
    sqlite_conn = open_sqlite_readonly(sqlite_source)
    try:
        left = sqlite_schema(sqlite_conn)
    finally:
        sqlite_conn.close()
    right = postgres_schema(postgres_engine(postgres_url))
    differences: list[dict] = []
    for table in sorted(set(left) | set(right)):
        if table not in left:
            differences.append({"level": "WARN", "table": table, "issue": "missing_from_sqlite"})
            continue
        if table not in right:
            differences.append({"level": "FAIL", "table": table, "issue": "missing_from_postgres"})
            continue
        left_columns = {column["name"]: column for column in left[table]["columns"]}
        right_columns = {column["name"]: column for column in right[table]["columns"]}
        for column in sorted(set(left_columns) | set(right_columns)):
            if column not in left_columns:
                differences.append({"level": "WARN", "table": table, "column": column, "issue": "column_missing_from_sqlite"})
                continue
            if column not in right_columns:
                differences.append({"level": "FAIL", "table": table, "column": column, "issue": "column_missing_from_postgres"})
                continue
            if normalize_type(left_columns[column]["type"]) != normalize_type(right_columns[column]["type"]):
                differences.append(
                    {
                        "level": "WARN",
                        "table": table,
                        "column": column,
                        "issue": "type_diff",
                        "sqlite_type": left_columns[column]["type"],
                        "postgres_type": right_columns[column]["type"],
                    }
                )
    return {"status": "FAIL" if any(d["level"] == "FAIL" for d in differences) else "PASS", "differences": differences}


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare ClipForge SQLite and PostgreSQL schemas without modifying either database.")
    parser.add_argument("--sqlite-url", required=True, help="SQLite path or sqlite:/// URL")
    parser.add_argument("--postgres-url", required=True, help="PostgreSQL URL; value is not printed")
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    report = compare_schema(args.sqlite_url, args.postgres_url)
    report["postgres_target"] = redact_url(args.postgres_url)
    if args.json_output:
        args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(report["status"])
    for diff in report["differences"]:
        print(json.dumps(diff, ensure_ascii=False, sort_keys=True))
    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
