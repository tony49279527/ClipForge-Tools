from __future__ import annotations

import importlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts" / "v3"


def _import_script(name: str):
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(SCRIPTS_DIR))


def _create_wal_sqlite(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, status TEXT, note TEXT)")
    conn.execute("CREATE TABLE v3_projects (id INTEGER PRIMARY KEY, project_status TEXT)")
    conn.execute("INSERT INTO jobs (id, status, note) VALUES (1, 'queued', NULL)")
    conn.execute("INSERT INTO jobs (id, status, note) VALUES (2, 'succeeded', 'done')")
    conn.execute("INSERT INTO v3_projects (id, project_status) VALUES (1, 'draft')")
    conn.commit()
    conn.close()


def test_sqlite_snapshot_wal_integrity_counts_keys_hash_and_nulls(tmp_path):
    snapshot = _import_script("create_consistent_sqlite_snapshot")
    source = tmp_path / "source.db"
    output = tmp_path / "snapshot.db"
    manifest_path = tmp_path / "manifest.json"
    _create_wal_sqlite(source)

    manifest = snapshot.create_snapshot(source=source, output=output, manifest_path=manifest_path)
    report = snapshot.validate_snapshot(source=source, snapshot=output)

    assert output.exists()
    assert manifest["snapshot"]["integrity_check"] == "ok"
    assert len(manifest["snapshot"]["sha256"]) == 64
    assert manifest["tables"]["jobs"] == 2
    assert manifest["primary_keys"]["jobs"] == [1, 2]
    assert manifest["null_counts"]["jobs"]["note"] == 1
    assert manifest["jobs_status"] == {"queued": 1, "succeeded": 1}
    assert report["status"] == "PASS"
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["snapshot"]["path"] == "snapshot.db"


def test_sqlite_snapshot_excludes_uncommitted_transaction_and_source_remains_usable(tmp_path):
    snapshot = _import_script("create_consistent_sqlite_snapshot")
    source = tmp_path / "source.db"
    output = tmp_path / "snapshot.db"
    _create_wal_sqlite(source)
    writer = sqlite3.connect(source)
    writer.execute("BEGIN")
    writer.execute("INSERT INTO jobs (id, status, note) VALUES (3, 'uncommitted', 'hidden')")

    manifest = snapshot.create_snapshot(source=source, output=output)
    copied = sqlite3.connect(output)
    try:
        assert copied.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2
        assert manifest["tables"]["jobs"] == 2
    finally:
        copied.close()
        writer.rollback()
        writer.execute("INSERT INTO jobs (id, status, note) VALUES (3, 'committed', 'visible')")
        writer.commit()
        assert writer.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 3
        writer.close()


def test_sqlite_snapshot_refuses_existing_target_same_path_and_cleans_failed_output(tmp_path, monkeypatch):
    snapshot = _import_script("create_consistent_sqlite_snapshot")
    source = tmp_path / "source.db"
    output = tmp_path / "snapshot.db"
    _create_wal_sqlite(source)
    output.write_bytes(b"existing")
    with pytest.raises(FileExistsError):
        snapshot.create_snapshot(source=source, output=output)
    with pytest.raises(ValueError):
        snapshot.create_snapshot(source=source, output=source)
    output.unlink()

    original_build_manifest = snapshot.build_manifest

    def fail_manifest(path):
        if path.name.startswith("snapshot.db.") and path.name.endswith(".tmp"):
            raise RuntimeError("forced failure")
        return original_build_manifest(path)

    monkeypatch.setattr(snapshot, "build_manifest", fail_manifest)
    with pytest.raises(RuntimeError):
        snapshot.create_snapshot(source=source, output=output)
    assert not output.exists()


def _preflight_state(**overrides):
    state = {
        "project": "gen-lang-client-0817070175",
        "region": "us-central1",
        "service": "clipforge-tools",
        "concurrency": 1,
        "max_instances": "1",
        "maintenance_mode": True,
        "current_backend": "sqlite",
        "gcsfuse_mount": True,
        "cloudsql_client_iam": True,
        "cloudsql_instance_exists": True,
        "postgres_version": "POSTGRES_16",
        "target_database_empty": True,
        "backups_configured": True,
        "cloudsql_region": "us-central1",
        "database_password_secret": True,
        "production_database_url_present": False,
        "git_head_matches": True,
        "git_clean": True,
        "snapshot_manifest_ok": True,
        "password": "do-not-print",
    }
    state.update(overrides)
    return state


@pytest.mark.parametrize(
    ("field", "value", "check_name"),
    [
        ("project", "wrong-project", "project"),
        ("concurrency", 80, "concurrency"),
        ("max_instances", "20", "max_instances"),
        ("maintenance_mode", False, "maintenance_mode"),
        ("snapshot_manifest_ok", False, "snapshot_manifest"),
        ("target_database_empty", False, "target_database_empty"),
        ("database_password_secret", False, "database_password_secret"),
        ("cloudsql_client_iam", False, "cloudsql_client_iam"),
    ],
)
def test_preflight_critical_failures(field, value, check_name):
    preflight = _import_script("preflight_production_postgres_cutover")
    report = preflight.evaluate_preflight(_preflight_state(**{field: value}))
    assert report["status"] == "FAIL"
    failed = {check["name"] for check in report["checks"] if check["level"] == "FAIL"}
    assert check_name in failed


def test_preflight_all_pass_and_output_does_not_include_password(capsys):
    preflight = _import_script("preflight_production_postgres_cutover")
    report = preflight.evaluate_preflight(_preflight_state())
    assert report["status"] == "PASS"
    print(json.dumps(report))
    assert "do-not-print" not in capsys.readouterr().out


def test_cutover_dry_run_plan_is_safe_and_forbids_partial_traffic():
    script = SCRIPTS_DIR / "cutover_sqlite_to_cloudsql.py"
    result = subprocess.run([sys.executable, str(script)], cwd=REPO_ROOT, text=True, capture_output=True, check=True)
    output = result.stdout
    assert "DRY_RUN" in output
    assert "Deploy a tagged PostgreSQL revision at 0% traffic." in output
    assert "Switch 100% traffic in one step" in output
    assert "PARTIAL_TRAFFIC_FOR_DATABASE_CUTOVER=FORBIDDEN" in output
    assert " 5%" not in output and " 25%" not in output and " 50%" not in output


def test_cutover_execute_requires_flags_and_terminal_confirmation(monkeypatch, capsys):
    cutover = _import_script("cutover_sqlite_to_cloudsql")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cutover",
            "--execute",
            "--project",
            "gen-lang-client-0817070175",
            "--region",
            "us-central1",
            "--service",
            "clipforge-tools",
        ],
    )
    assert cutover.main() == 2
    assert "REFUSED" in capsys.readouterr().out

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cutover",
            "--execute",
            "--project",
            "gen-lang-client-0817070175",
            "--region",
            "us-central1",
            "--service",
            "clipforge-tools",
            "--confirm",
            "I_UNDERSTAND_THIS_CHANGES_PRODUCTION_DATABASE",
        ],
    )
    monkeypatch.setattr("builtins.input", lambda prompt: "NO")
    assert cutover.main() == 2
    assert "Terminal confirmation did not match" in capsys.readouterr().out


def test_rollback_plan_allows_sqlite_only_before_postgres_writes():
    rollback = _import_script("plan_postgres_cutover_rollback")
    before = rollback.build_rollback_plan(postgres_has_new_writes=False)
    after = rollback.build_rollback_plan(postgres_has_new_writes=True)

    assert before["status"] == "SQLITE_ROLLBACK_ALLOWED_BEFORE_NEW_WRITES"
    assert any("Route 100% traffic back" in item for item in before["allowed"])
    assert after["status"] == "BLOCK_DIRECT_SQLITE_ROLLBACK"
    assert any("Do not route traffic directly back to SQLite" in item for item in after["forbidden"])
    assert any("Preserve PostgreSQL data" in item for item in after["allowed"])
