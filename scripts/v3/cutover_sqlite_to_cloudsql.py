from __future__ import annotations

import argparse
import sys


CONFIRM_TEXT = "I_UNDERSTAND_THIS_CHANGES_PRODUCTION_DATABASE"
TERMINAL_CONFIRM = "YES_SWITCH_CLIPFORGE_DATABASE_ONCE"
EXPECTED_PROJECT = "gen-lang-client-0817070175"
EXPECTED_REGION = "us-central1"
EXPECTED_SERVICE = "clipforge-tools"


def build_cutover_plan() -> list[dict[str, str]]:
    return [
        {"step": "verify_maintenance_mode", "action": "Verify maintenance mode is enabled."},
        {"step": "verify_write_freeze", "action": "Verify all write paths and workers are frozen."},
        {"step": "pause_worker", "action": "Pause or stop RQ workers before database cutover."},
        {"step": "create_sqlite_snapshot", "action": "Create a consistent SQLite snapshot with sqlite3 backup API."},
        {"step": "upload_timestamped_backup", "action": "Upload timestamped SQLite backup; do not delete existing SQLite."},
        {"step": "validate_snapshot", "action": "Validate integrity, row counts, hashes, and manifest."},
        {"step": "verify_cloudsql_schema", "action": "Verify target Cloud SQL PostgreSQL schema."},
        {"step": "export_sqlite", "action": "Export SQLite rows to neutral JSONL in a temporary location."},
        {"step": "dry_run_import", "action": "Run PostgreSQL import dry-run."},
        {"step": "execute_import", "action": "Import into PostgreSQL in one transaction."},
        {"step": "validate_migration", "action": "Run migration validation and sequence checks."},
        {"step": "database_url_secret", "action": "Use Secret Manager for DATABASE_URL; never plain env."},
        {"step": "deploy_tagged_revision_zero_traffic", "action": "Deploy a tagged PostgreSQL revision at 0% traffic."},
        {"step": "add_cloudsql_connection", "action": "Attach Cloud SQL connection to the tagged revision."},
        {"step": "set_database_url_secret", "action": "Set Secret-referenced DATABASE_URL on the tagged revision."},
        {"step": "keep_new_revision_maintenance", "action": "Keep maintenance mode enabled on the PostgreSQL revision."},
        {"step": "tag_readonly_checks", "action": "Run read-only checks through the tag URL."},
        {"step": "verify_postgresql_backend", "action": "Confirm backend reports postgresql."},
        {"step": "switch_traffic_once", "action": "Switch 100% traffic in one step; do not use 5/25/50 partial traffic."},
        {"step": "final_readonly_checks", "action": "Run final read-only health checks."},
        {"step": "manual_unfreeze", "action": "Only after human approval, disable maintenance mode."},
        {"step": "preserve_sqlite_backup", "action": "Do not delete the SQLite backup."},
    ]


def validate_execute_args(args: argparse.Namespace) -> None:
    if not args.execute:
        return
    if args.project != EXPECTED_PROJECT or args.region != EXPECTED_REGION or args.service != EXPECTED_SERVICE:
        raise ValueError("Execute mode is restricted to the known ClipForge production project, region, and service.")
    if args.confirm != CONFIRM_TEXT:
        raise ValueError("Missing production database change confirmation flag.")
    typed = input("Type YES_SWITCH_CLIPFORGE_DATABASE_ONCE to continue: ").strip()
    if typed != TERMINAL_CONFIRM:
        raise ValueError("Terminal confirmation did not match; aborting.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run first cutover orchestrator for ClipForge SQLite to Cloud SQL.")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--project", default="")
    parser.add_argument("--region", default="")
    parser.add_argument("--service", default="")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    try:
        validate_execute_args(args)
    except ValueError as exc:
        print(f"REFUSED: {exc}")
        return 2
    if args.execute:
        print("EXECUTE MODE IS NOT IMPLEMENTED IN THIS SAFETY TOOL YET.")
        return 3
    print("DRY_RUN")
    for index, step in enumerate(build_cutover_plan(), start=1):
        print(f"{index:02d}. {step['step']}: {step['action']}")
    print("PARTIAL_TRAFFIC_FOR_DATABASE_CUTOVER=FORBIDDEN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
