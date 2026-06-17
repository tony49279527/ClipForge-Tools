from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts" / "v3"


def _clear_modules() -> None:
    for name in list(sys.modules):
        if name == "db" or name.startswith("clipforge_v3"):
            sys.modules.pop(name, None)


@pytest.fixture()
def sqlite_sample(monkeypatch, tmp_path):
    _clear_modules()
    db_path = tmp_path / "clipforge.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_URL", raising=False)
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    db = importlib.import_module("db")
    db.init_db()
    migrations = importlib.import_module("clipforge_v3.migrations")
    project_repo = importlib.import_module("clipforge_v3.repositories.project_repository")
    asset_repo = importlib.import_module("clipforge_v3.repositories.asset_repository")
    shot_repo = importlib.import_module("clipforge_v3.repositories.shot_repository")
    migrations.ensure_v3_schema()
    job_id = db.create_job(
        {
            "project_name": "export job",
            "product_name": "wheel",
            "product_brief": "brief",
            "video_mode": "single",
            "ratio": "16:9",
            "clip_duration": 5,
            "clip_count": 1,
            "resolution": "720p",
            "youtube_title": "title",
            "privacy": "private",
        }
    )
    project_id = project_repo.create_project(
        {
            "project_name": "export project",
            "product_name": "wheel",
            "product_category": "tool",
            "target_market": "US",
            "target_audience": "DIY",
            "target_platform": "amazon",
            "aspect_ratio": "16:9",
            "total_duration": 5,
            "default_clip_duration": 5,
            "resolution": "720p",
            "language": "en",
        }
    )
    asset_repo.create_asset(
        {
            "project_id": project_id,
            "asset_type": "image",
            "original_filename": "wheel.jpg",
            "mime_type": "image/jpeg",
            "primary_role": "identity_anchor",
        }
    )
    shot_id = shot_repo.create_shot(
        {
            "project_id": project_id,
            "shot_id": "S01",
            "sequence_index": 1,
            "purpose": "demo",
            "mode": "single_shot",
            "duration": 5,
            "primary_spend": "mock",
            "subject_action": "rotate",
            "generation_strategy": "mock",
        }
    )
    prompt_id = shot_repo.create_prompt_version(
        {
            "shot_id": shot_id,
            "version": 1,
            "mode": "mock",
            "prompt_text": "prompt",
            "prompt_char_count": 6,
            "prompt_language": "en",
        }
    )
    project_repo.create_preflight_check(
        {
            "project_id": project_id,
            "shot_id": shot_id,
            "prompt_version_id": prompt_id,
            "tier": "draft",
            "allow_submit": True,
            "result_json": {"ok": True},
        }
    )
    return {"db_path": db_path, "job_id": job_id, "project_id": project_id}


def test_export_sqlite_manifest_and_jsonl(sqlite_sample, tmp_path):
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        exporter = importlib.import_module("export_sqlite_for_postgres")
        output_dir = tmp_path / "export"
        manifest = exporter.export_sqlite(str(sqlite_sample["db_path"]), output_dir)
    finally:
        sys.path.remove(str(SCRIPTS_DIR))

    assert (output_dir / "manifest.json").exists()
    tables = {entry["table"]: entry for entry in manifest["tables"]}
    assert tables["jobs"]["row_count"] == 1
    assert tables["v3_projects"]["row_count"] == 1
    assert len(tables["jobs"]["sha256"]) == 64
    first_job = json.loads((output_dir / "jobs.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert first_job["project_name"] == "export job"


def test_export_refuses_to_overwrite(sqlite_sample, tmp_path):
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        exporter = importlib.import_module("export_sqlite_for_postgres")
        output_dir = tmp_path / "export"
        exporter.export_sqlite(str(sqlite_sample["db_path"]), output_dir)
        with pytest.raises(FileExistsError):
            exporter.export_sqlite(str(sqlite_sample["db_path"]), output_dir)
    finally:
        sys.path.remove(str(SCRIPTS_DIR))


def test_import_tool_rejects_sqlite_and_nonlocal_targets(sqlite_sample, tmp_path):
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        exporter = importlib.import_module("export_sqlite_for_postgres")
        importer = importlib.import_module("import_postgres_from_export")
        output_dir = tmp_path / "export"
        exporter.export_sqlite(str(sqlite_sample["db_path"]), output_dir)
        with pytest.raises(ValueError):
            importer.import_export(output_dir, "sqlite:///tmp/not-postgres.db", dry_run=True, validate_only=False, confirmation=None)
        with pytest.raises(ValueError):
            importer.import_export(
                output_dir,
                "postgresql+psycopg://dbuser:secret@prod.example.com/clipforge",
                dry_run=True,
                validate_only=False,
                confirmation=None,
            )
    finally:
        sys.path.remove(str(SCRIPTS_DIR))


def test_migration_scripts_help():
    for script in (
        "compare_database_schema.py",
        "export_sqlite_for_postgres.py",
        "import_postgres_from_export.py",
        "validate_database_migration.py",
    ):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / script), "--help"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
        assert "usage:" in result.stdout


@pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_DATABASE_URL"),
    reason="Set POSTGRES_TEST_DATABASE_URL to run PostgreSQL migration rehearsal.",
)
def test_postgresql_export_import_validate_rehearsal(sqlite_sample, tmp_path, monkeypatch):
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        exporter = importlib.import_module("export_sqlite_for_postgres")
        importer = importlib.import_module("import_postgres_from_export")
        validator = importlib.import_module("validate_database_migration")
        comparer = importlib.import_module("compare_database_schema")
        output_dir = tmp_path / "export"
        exporter.export_sqlite(str(sqlite_sample["db_path"]), output_dir)
        database_url = os.environ["POSTGRES_TEST_DATABASE_URL"]
        _clear_modules()
        monkeypatch.setenv("DATABASE_URL", database_url)
        db = importlib.import_module("db")
        with db.get_engine().begin() as conn:
            conn.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
            conn.exec_driver_sql("CREATE SCHEMA public")
        db.reset_engine_for_tests()
        db = importlib.reload(db)
        db.init_db()
        importlib.import_module("clipforge_v3.migrations").ensure_v3_schema()
        dry_run = importer.import_export(output_dir, database_url, dry_run=True, validate_only=False, confirmation=None)
        assert dry_run["dry_run"] is True
        importer.import_export(
            output_dir,
            database_url,
            dry_run=False,
            validate_only=False,
            confirmation=importer.CONFIRMATION,
        )
        report = validator.validate_migration(output_dir, database_url)
        assert report["status"] == "PASS"
        schema_report = comparer.compare_schema(str(sqlite_sample["db_path"]), database_url)
        assert schema_report["status"] == "PASS"
    finally:
        if "db" in locals():
            db.get_engine().dispose()
        sys.path.remove(str(SCRIPTS_DIR))
