from __future__ import annotations

import argparse
import json


def build_rollback_plan(*, postgres_has_new_writes: bool) -> dict:
    if postgres_has_new_writes:
        return {
            "status": "BLOCK_DIRECT_SQLITE_ROLLBACK",
            "allowed": [
                "Keep maintenance mode enabled.",
                "Freeze all writes.",
                "Compare PostgreSQL and SQLite differences.",
                "Create a reverse-sync or manual reconciliation plan.",
                "Preserve PostgreSQL data.",
            ],
            "forbidden": [
                "Do not route traffic directly back to SQLite.",
                "Do not discard PostgreSQL writes.",
                "Do not allow dual-write without reconciliation.",
            ],
        }
    return {
        "status": "SQLITE_ROLLBACK_ALLOWED_BEFORE_NEW_WRITES",
        "allowed": [
            "Route 100% traffic back to the original SQLite revision.",
            "Keep maintenance mode enabled.",
            "Validate the SQLite snapshot.",
            "Keep PostgreSQL for inspection.",
        ],
        "forbidden": [
            "Do not delete PostgreSQL immediately.",
            "Do not disable maintenance mode until validation passes.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan rollback boundaries for ClipForge PostgreSQL cutover.")
    parser.add_argument("--postgres-has-new-writes", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build_rollback_plan(postgres_has_new_writes=args.postgres_has_new_writes), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
