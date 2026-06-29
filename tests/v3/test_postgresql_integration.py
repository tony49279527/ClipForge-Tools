from __future__ import annotations

import importlib
import json
import os
import sys
import threading
from typing import Any

import pytest


pytestmark = pytest.mark.skipif(
    not os.getenv("POSTGRES_TEST_DATABASE_URL"),
    reason="Set POSTGRES_TEST_DATABASE_URL to run PostgreSQL integration tests.",
)


def _clear_modules() -> None:
    for name in list(sys.modules):
        if name == "db" or name == "app" or name.startswith("clipforge_v3"):
            sys.modules.pop(name, None)


@pytest.fixture()
def pg_modules(monkeypatch, tmp_path):
    database_url = os.environ["POSTGRES_TEST_DATABASE_URL"]
    if "clipforge" not in database_url and "test" not in database_url:
        pytest.fail("POSTGRES_TEST_DATABASE_URL must point to a disposable ClipForge test database")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OUTPUTS_DIR", str(tmp_path / "outputs"))
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("CLIPFORGE_V3_ENABLED", "true")
    _clear_modules()
    db = importlib.import_module("db")
    modules = None
    try:
        with db.get_engine().begin() as conn:
            conn.exec_driver_sql("DROP SCHEMA IF EXISTS public CASCADE")
            conn.exec_driver_sql("CREATE SCHEMA public")
        db.reset_engine_for_tests()
        db = importlib.reload(db)
        db.init_db()
        migrations = importlib.import_module("clipforge_v3.migrations")
        migrations.ensure_v3_schema()
        migrations.ensure_v3_schema()
        modules = {
            "db": db,
            "migrations": migrations,
            "project_repo": importlib.import_module("clipforge_v3.repositories.project_repository"),
            "asset_repo": importlib.import_module("clipforge_v3.repositories.asset_repository"),
            "shot_repo": importlib.import_module("clipforge_v3.repositories.shot_repository"),
            "take_repo": importlib.import_module("clipforge_v3.repositories.take_repository"),
        }
        yield modules
    finally:
        (modules["db"] if modules else db).get_engine().dispose()


def _project_payload() -> dict[str, Any]:
    return {
        "project_name": "PostgreSQL integration",
        "product_name": "Buffing wheel",
        "product_category": "tool accessory",
        "target_market": "US",
        "target_audience": "DIY users",
        "target_platform": "amazon",
        "aspect_ratio": "16:9",
        "total_duration": 5,
        "default_clip_duration": 5,
        "resolution": "720p",
        "language": "en",
    }


def _shot_payload(project_id: int, sequence_index: int = 1) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "shot_id": f"S{sequence_index:02d}",
        "sequence_index": sequence_index,
        "purpose": "show product",
        "mode": "single_shot",
        "duration": 5,
        "primary_spend": "real_provider",
        "economized_json": [],
        "subject_action": "product rotates once",
        "start_state_json": {},
        "end_state_json": {},
        "camera_contract_json": {"shot": "single"},
        "lighting_contract_json": {},
        "audio_contract_json": {},
        "reference_roles_json": [],
        "continuity_anchors_json": {},
        "constraints_json": [],
        "risk_codes_json": [],
        "generation_strategy": "i2v",
        "status": "planned",
    }


def _prompt_payload(shot_id: int) -> dict[str, Any]:
    return {
        "shot_id": shot_id,
        "version": 1,
        "mode": "seedance",
        "prompt_text": "Single product hero shot, 5 seconds, 720p.",
        "prompt_char_count": 43,
        "prompt_language": "en",
        "role_map_json": {},
        "compiler_warnings_json": [],
        "validation_result_json": {"ok": True},
        "allow_submit": True,
    }


def _create_core_records(pg_modules):
    project_repo = pg_modules["project_repo"]
    asset_repo = pg_modules["asset_repo"]
    shot_repo = pg_modules["shot_repo"]
    project_id = project_repo.create_project(_project_payload())
    truth_id = project_repo.create_product_truth(
        {
            "project_id": project_id,
            "source_description": "round buffing wheel",
            "product_truth_json": {"shape": "round"},
            "immutable_geometry_json": {"shape": "round"},
            "dimensions_json": {"diameter": "6 inch"},
            "material_json": {"material": "cotton"},
            "colors_json": {"primary": "white"},
            "components_json": [],
            "installation_rules_json": [],
            "working_surface_json": {},
            "allowed_behaviors_json": [],
            "forbidden_transformations_json": [],
            "forbidden_materials_json": [],
            "safety_constraints_json": [],
            "confidence_json": {},
        }
    )
    asset_id = asset_repo.create_asset(
        {
            "project_id": project_id,
            "asset_type": "image",
            "original_filename": "product.jpg",
            "mime_type": "image/jpeg",
            "primary_role": "identity_anchor",
            "must_transfer_json": ["shape"],
            "must_not_transfer_json": [],
            "applies_to_shots_json": [],
            "metadata_json": {},
            "remote_url": "https://assets.example/product.jpg",
            "storage_backend": "r2",
            "object_key": "projects/1/assets/product.jpg",
            "access_url": "https://public.example/projects/1/assets/product.jpg",
        }
    )
    shot_id = shot_repo.create_shot(_shot_payload(project_id))
    prompt_id = shot_repo.create_prompt_version(_prompt_payload(shot_id))
    preflight_id = project_repo.create_preflight_check(
        {
            "project_id": project_id,
            "shot_id": shot_id,
            "prompt_version_id": prompt_id,
            "tier": "draft",
            "allow_submit": True,
            "result_json": {"allow_submit": True},
        }
    )
    operation_id = project_repo.create_operation_event(
        {
            "request_id": "pg-integration",
            "project_id": project_id,
            "shot_id": shot_id,
            "stage": "test",
            "status": "succeeded",
        }
    )
    return {
        "project_id": project_id,
        "truth_id": truth_id,
        "asset_id": asset_id,
        "shot_id": shot_id,
        "prompt_id": prompt_id,
        "preflight_id": preflight_id,
        "operation_id": operation_id,
    }


def test_postgresql_schema_legacy_v3_and_constraints(pg_modules):
    db = pg_modules["db"]
    rows = db.select_all(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = CURRENT_SCHEMA()
        """
    )
    tables = {row["table_name"] for row in rows}
    expected = {
        "jobs",
        "clips",
        "usage_events",
        "v3_projects",
        "v3_assets",
        "v3_shots",
        "v3_prompt_versions",
        "v3_preflight_checks",
        "v3_generation_submissions",
        "v3_takes",
        "v3_usage_events",
        "v3_operation_events",
    }
    assert expected.issubset(tables)

    indexes = {
        row["indexname"]
        for row in db.select_all(
            "SELECT indexname FROM pg_indexes WHERE schemaname = CURRENT_SCHEMA() AND tablename LIKE 'v3_%'"
        )
    }
    assert "idx_v3_generation_submissions_idempotency" in indexes
    assert "idx_v3_takes_generation_submission" in indexes
    assert "idx_v3_usage_events_event_key" in indexes


def test_postgresql_legacy_job_crud_and_rollback(pg_modules):
    db = pg_modules["db"]
    job_id = db.create_job(
        {
            "project_name": "pg legacy",
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
    assert db.get_job_by_id(job_id)["project_name"] == "pg legacy"
    db.update_job_fields(job_id, {"status": "running"})
    db.update_job_fields(job_id, {"status": "running"})
    assert db.get_job_by_id(job_id)["status"] == "running"
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO clips (job_id, clip_index, status, created_at, updated_at) VALUES (:job_id, :clip_index, :status, :created_at, :updated_at)",
                {"job_id": job_id, "clip_index": 1, "status": "queued", "created_at": db.utc_now(), "updated_at": db.utc_now()},
            )
            raise RuntimeError("rollback")
    assert db.select_one("SELECT COUNT(*) AS count FROM clips WHERE job_id = :job_id", {"job_id": job_id})["count"] == 0
    assert db.fetch_one("SELECT status FROM jobs WHERE id = ?", (job_id,))["status"] == "running"


def test_postgresql_v3_basic_repository_paths(pg_modules):
    db = pg_modules["db"]
    project_repo = pg_modules["project_repo"]
    asset_repo = pg_modules["asset_repo"]
    shot_repo = pg_modules["shot_repo"]
    ids = _create_core_records(pg_modules)
    project_repo.update_project(ids["project_id"], {"current_stage": "director_plan"})
    asset_repo.update_asset(ids["asset_id"], {"user_approved": 1})
    shot_repo.update_shot(ids["shot_id"], {"status": "compiled"})
    assert project_repo.get_project(ids["project_id"])["current_stage"] == "director_plan"
    assert asset_repo.get_asset(ids["asset_id"])["user_approved"] == 1
    assert shot_repo.get_shot(ids["shot_id"])["status"] == "compiled"
    assert db.select_one("SELECT COUNT(*) AS count FROM v3_preflight_checks")["count"] == 1
    assert db.select_one("SELECT COUNT(*) AS count FROM v3_operation_events")["count"] == 1


def test_postgresql_generation_submission_take_usage_idempotency(pg_modules):
    db = pg_modules["db"]
    take_repo = pg_modules["take_repo"]
    project_repo = pg_modules["project_repo"]
    ids = _create_core_records(pg_modules)
    submission_payload = {
        "project_id": ids["project_id"],
        "shot_id": ids["shot_id"],
        "prompt_version_id": ids["prompt_id"],
        "generation_tier": "draft",
        "provider": "ark",
        "idempotency_key": "pg-idempotency-key",
        "provider_request_hash": "hash",
        "submission_status": "unknown_submission_state",
        "paid_confirmed": True,
        "budget_approved_at": db.utc_now(),
        "request_payload_json": {"model": "seedance"},
    }
    first, created_first = take_repo.reserve_generation_submission(submission_payload)
    second, created_second = take_repo.reserve_generation_submission(submission_payload)
    assert created_first is True
    assert created_second is False
    assert first["id"] == second["id"]
    assert second["submission_status"] == "unknown_submission_state"
    take_repo.update_generation_submission(first["id"], {"provider_task_id": "cgt-postgres", "submission_status": "succeeded"})
    submission = take_repo.get_generation_submission(first["id"])
    assert submission["provider_task_id"] == "cgt-postgres"

    take, created_take = take_repo.get_or_create_take_for_submission(
        {
            "shot_id": ids["shot_id"],
            "take_number": 1,
            "prompt_version_id": ids["prompt_id"],
            "status": "completed",
            "local_path": "outputs/pg.mp4",
            "generation_submission_id": first["id"],
        }
    )
    replay_take, replay_created = take_repo.get_or_create_take_for_submission(
        {
            "shot_id": ids["shot_id"],
            "take_number": 2,
            "prompt_version_id": ids["prompt_id"],
            "status": "completed",
            "local_path": "outputs/pg-duplicate.mp4",
            "generation_submission_id": first["id"],
        }
    )
    assert created_take is True
    assert replay_created is False
    assert replay_take["id"] == take["id"]
    take_repo.update_generation_submission(first["id"], {"take_id": take["id"], "submission_status": "succeeded"})
    event_key = f"provider_generation:{first['id']}"
    usage_id = project_repo.create_usage_event(
        {
            "project_id": ids["project_id"],
            "shot_id": ids["shot_id"],
            "take_id": take["id"],
            "stage": "generation",
            "provider": "ark",
            "model": "seedance",
            "duration": 5,
            "resolution": "720p",
            "estimated_cost": 1.25,
            "status": "succeeded",
            "event_key": event_key,
            "source_type": "generation_submission",
            "source_id": first["id"],
        }
    )
    duplicate_usage_id = project_repo.create_usage_event(
        {
            "project_id": ids["project_id"],
            "shot_id": ids["shot_id"],
            "take_id": take["id"],
            "stage": "generation",
            "provider": "ark",
            "model": "seedance",
            "estimated_cost": 1.25,
            "status": "succeeded",
            "event_key": event_key,
            "source_type": "generation_submission",
            "source_id": first["id"],
        }
    )
    assert duplicate_usage_id == usage_id
    assert db.select_one("SELECT COUNT(*) AS count FROM v3_usage_events WHERE event_key = :event_key", {"event_key": event_key})["count"] == 1
    assert take_repo.get_generation_submission(first["id"])["take_id"] == take["id"]


def test_postgresql_concurrent_idempotent_writes(pg_modules):
    db = pg_modules["db"]
    take_repo = pg_modules["take_repo"]
    project_repo = pg_modules["project_repo"]
    ids = _create_core_records(pg_modules)
    submission_payload = {
        "project_id": ids["project_id"],
        "shot_id": ids["shot_id"],
        "prompt_version_id": ids["prompt_id"],
        "generation_tier": "draft",
        "provider": "ark",
        "idempotency_key": "pg-concurrent-submission",
        "provider_request_hash": "hash",
        "submission_status": "reserved",
        "paid_confirmed": True,
        "budget_approved_at": db.utc_now(),
    }
    barrier = threading.Barrier(2)
    results: list[tuple[dict[str, Any], bool]] = []

    def reserve_worker() -> None:
        barrier.wait()
        results.append(take_repo.reserve_generation_submission(submission_payload))

    threads = [threading.Thread(target=reserve_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 2
    assert sum(1 for _row, created in results if created) == 1
    submission_id = results[0][0]["id"]
    assert db.select_one("SELECT COUNT(*) AS count FROM v3_generation_submissions WHERE idempotency_key = :key", {"key": "pg-concurrent-submission"})["count"] == 1

    take_barrier = threading.Barrier(2)
    take_results: list[tuple[dict[str, Any], bool]] = []

    def take_worker(take_number: int) -> None:
        take_barrier.wait()
        take_results.append(
            take_repo.get_or_create_take_for_submission(
                {
                    "shot_id": ids["shot_id"],
                    "take_number": take_number,
                    "prompt_version_id": ids["prompt_id"],
                    "status": "completed",
                    "local_path": f"outputs/pg-{take_number}.mp4",
                    "generation_submission_id": submission_id,
                }
            )
        )

    take_threads = [threading.Thread(target=take_worker, args=(index,)) for index in (1, 2)]
    for thread in take_threads:
        thread.start()
    for thread in take_threads:
        thread.join()
    assert sum(1 for _row, created in take_results if created) == 1
    assert db.select_one("SELECT COUNT(*) AS count FROM v3_takes WHERE generation_submission_id = :submission_id", {"submission_id": submission_id})["count"] == 1

    usage_barrier = threading.Barrier(2)
    usage_results: list[int] = []
    event_key = f"provider_generation:{submission_id}"

    def usage_worker() -> None:
        usage_barrier.wait()
        usage_results.append(
            project_repo.create_usage_event(
                {
                    "project_id": ids["project_id"],
                    "shot_id": ids["shot_id"],
                    "take_id": take_results[0][0]["id"],
                    "stage": "generation",
                    "provider": "ark",
                    "model": "seedance",
                    "status": "succeeded",
                    "event_key": event_key,
                    "source_type": "generation_submission",
                    "source_id": submission_id,
                }
            )
        )

    usage_threads = [threading.Thread(target=usage_worker) for _ in range(2)]
    for thread in usage_threads:
        thread.start()
    for thread in usage_threads:
        thread.join()
    assert len(set(usage_results)) == 1
    assert db.select_one("SELECT COUNT(*) AS count FROM v3_usage_events WHERE event_key = :event_key", {"event_key": event_key})["count"] == 1
