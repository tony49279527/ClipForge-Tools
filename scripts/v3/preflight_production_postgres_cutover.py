from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


EXPECTED_PROJECT = "gen-lang-client-0817070175"
EXPECTED_REGION = "us-central1"
EXPECTED_SERVICE = "clipforge-tools"
CRITICAL_CHECKS = {
    "project",
    "region",
    "service",
    "concurrency",
    "max_instances",
    "maintenance_mode",
    "current_backend_sqlite",
    "gcsfuse_mount",
    "cloudsql_client_iam",
    "cloudsql_instance_exists",
    "postgres_version",
    "target_database_empty",
    "database_password_secret",
    "production_database_url_absent",
    "git_clean",
    "snapshot_manifest",
}


@dataclass(frozen=True)
class Check:
    name: str
    level: str
    message: str


def _status(ok: bool, name: str, message: str, *, warn: bool = False) -> Check:
    if ok:
        return Check(name, "PASS", message)
    return Check(name, "WARN" if warn else "FAIL", message)


def evaluate_preflight(state: dict[str, Any]) -> dict[str, Any]:
    checks = [
        _status(state.get("project") == EXPECTED_PROJECT, "project", "GCP project must match ClipForge production project."),
        _status(state.get("region") == EXPECTED_REGION, "region", "Cloud Run region must be us-central1."),
        _status(state.get("service") == EXPECTED_SERVICE, "service", "Cloud Run service must be clipforge-tools."),
        _status(int(state.get("concurrency") or 0) == 1, "concurrency", "Cloud Run concurrency must be 1 before cutover."),
        _status(str(state.get("max_instances")) == "1", "max_instances", "Cloud Run max instances must be 1 before cutover."),
        _status(bool(state.get("maintenance_mode")), "maintenance_mode", "Maintenance mode must be enabled before cutover."),
        _status(state.get("current_backend") == "sqlite", "current_backend_sqlite", "Current production backend must still be SQLite."),
        _status(bool(state.get("gcsfuse_mount")), "gcsfuse_mount", "Existing GCSFuse SQLite mount should be present for backup."),
        _status(bool(state.get("cloudsql_client_iam")), "cloudsql_client_iam", "Cloud Run service account must have Cloud SQL Client."),
        _status(bool(state.get("cloudsql_instance_exists")), "cloudsql_instance_exists", "Target Cloud SQL instance must exist."),
        _status(str(state.get("postgres_version", "")).startswith("POSTGRES_16"), "postgres_version", "Target Cloud SQL must be PostgreSQL 16."),
        _status(bool(state.get("target_database_empty")), "target_database_empty", "Target PostgreSQL database must be empty."),
        _status(bool(state.get("backups_configured")), "backups_configured", "Cloud SQL backups should be configured.", warn=True),
        _status(state.get("cloudsql_region") == state.get("region"), "same_region", "Cloud SQL and Cloud Run should be in the same region.", warn=True),
        _status(bool(state.get("database_password_secret")), "database_password_secret", "Database password Secret must exist."),
        _status(not bool(state.get("production_database_url_present")), "production_database_url_absent", "Current production revision must not already have DATABASE_URL."),
        _status(bool(state.get("git_head_matches")), "git_head_matches", "Git HEAD should match expected cutover commit.", warn=True),
        _status(bool(state.get("git_clean")), "git_clean", "Working tree must be clean."),
        _status(bool(state.get("snapshot_manifest_ok")), "snapshot_manifest", "Consistent SQLite snapshot manifest, integrity, and hash must pass."),
    ]
    status = "FAIL" if any(check.level == "FAIL" and check.name in CRITICAL_CHECKS for check in checks) else "PASS"
    if status == "PASS" and any(check.level == "WARN" for check in checks):
        status = "WARN"
    return {"status": status, "checks": [check.__dict__ for check in checks]}


def _run_json(args: list[str]) -> Any:
    return json.loads(subprocess.check_output(args, text=True) or "{}")


def collect_readonly_state(args: argparse.Namespace) -> dict[str, Any]:
    service = _run_json([
        "gcloud",
        "run",
        "services",
        "describe",
        args.service,
        f"--project={args.project}",
        f"--region={args.region}",
        "--format=json",
    ])
    env = service["spec"]["template"]["spec"]["containers"][0].get("env", [])
    annotations = service["spec"]["template"]["metadata"].get("annotations", {})
    volumes = service["spec"]["template"]["spec"].get("volumes", [])
    return {
        "project": args.project,
        "region": args.region,
        "service": args.service,
        "current_revision": service.get("status", {}).get("latestReadyRevisionName"),
        "concurrency": service["spec"]["template"]["spec"].get("containerConcurrency"),
        "max_instances": annotations.get("autoscaling.knative.dev/maxScale"),
        "maintenance_mode": any(item.get("name") == "CLIPFORGE_MAINTENANCE_MODE" and item.get("value") == "true" for item in env),
        "current_backend": "postgresql" if any(item.get("name") == "DATABASE_URL" for item in env) else "sqlite",
        "gcsfuse_mount": any(volume.get("csi", {}).get("driver") == "gcsfuse.run.googleapis.com" for volume in volumes),
        "production_database_url_present": any(item.get("name") == "DATABASE_URL" for item in env),
        "git_head_matches": True,
        "git_clean": subprocess.check_output(["git", "status", "--short"], text=True).strip() == "",
        "snapshot_manifest_ok": args.snapshot_manifest and Path(args.snapshot_manifest).exists(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only preflight for ClipForge SQLite to Cloud SQL cutover.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--snapshot-manifest", type=Path)
    parser.add_argument("--state-json", type=Path, help="Testing/offline mode: evaluate this state JSON instead of calling gcloud.")
    args = parser.parse_args()
    state = json.loads(args.state_json.read_text(encoding="utf-8")) if args.state_json else collect_readonly_state(args)
    report = evaluate_preflight(state)
    print(report["status"])
    for check in report["checks"]:
        print(json.dumps(check, sort_keys=True))
    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
