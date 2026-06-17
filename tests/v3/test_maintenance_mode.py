from __future__ import annotations

import importlib
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


def _clear_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name == "db" or name == "task_queue" or name.startswith("clipforge_v3"):
            sys.modules.pop(name, None)


def _load_app(monkeypatch, tmp_path, *, maintenance: bool):
    data_dir = tmp_path / "data"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_URL", raising=False)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DB_PATH", str(data_dir / "clipforge.db"))
    monkeypatch.setenv("OUTPUTS_DIR", str(tmp_path / "outputs"))
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("YOUTUBE_ACCOUNTS_DIR", str(tmp_path / "youtube_accounts"))
    monkeypatch.setenv("CLIPFORGE_V3_ENABLED", "true")
    monkeypatch.setenv("CLIPFORGE_MAINTENANCE_MODE", "true" if maintenance else "false")
    monkeypatch.setenv("ARK_API_KEY", "secret-value-must-not-appear")
    _clear_modules()
    app_module = importlib.import_module("app")
    importlib.import_module("db").init_db()
    app_module.on_startup()
    return SimpleNamespace(app_module=app_module, client=TestClient(app_module.app))


def _assert_maintenance_response(response):
    assert response.status_code == 503
    assert response.json() == {
        "error": "maintenance_mode",
        "message": "ClipForge is temporarily read-only for database maintenance.",
    }
    assert "secret-value-must-not-appear" not in response.text


def test_maintenance_mode_defaults_to_off(monkeypatch):
    monkeypatch.delenv("CLIPFORGE_MAINTENANCE_MODE", raising=False)
    _clear_modules()
    service = importlib.import_module("clipforge_v3.services.maintenance_service")
    assert service.is_maintenance_mode() is False


def test_maintenance_mode_allows_get_pages_and_health(monkeypatch, tmp_path):
    loaded = _load_app(monkeypatch, tmp_path, maintenance=True)
    try:
        assert loaded.client.get("/").status_code == 200
        assert loaded.client.get("/jobs").status_code == 200
        assert loaded.client.get("/v2").status_code == 200
        assert loaded.client.get("/v3").status_code == 200
        assert loaded.client.get("/healthz").status_code == 200
        assert loaded.client.get("/v3/ready").status_code == 200
    finally:
        loaded.client.close()


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
def test_maintenance_mode_blocks_write_methods(monkeypatch, tmp_path, method):
    loaded = _load_app(monkeypatch, tmp_path, maintenance=True)
    try:
        response = getattr(loaded.client, method)("/api/templates/1")
        _assert_maintenance_response(response)
    finally:
        loaded.client.close()


def test_maintenance_mode_blocks_legacy_job_creation(monkeypatch, tmp_path):
    loaded = _load_app(monkeypatch, tmp_path, maintenance=True)
    try:
        response = loaded.client.post("/jobs", data={})
        _assert_maintenance_response(response)
    finally:
        loaded.client.close()


def test_maintenance_mode_blocks_v3_writes_and_uploads(monkeypatch, tmp_path):
    loaded = _load_app(monkeypatch, tmp_path, maintenance=True)
    try:
        _assert_maintenance_response(loaded.client.post("/v3/projects", data={}))
        _assert_maintenance_response(loaded.client.post("/v3/projects/1/assets", files={"asset_file": ("x.png", b"data", "image/png")}))
    finally:
        loaded.client.close()


def test_maintenance_mode_blocks_oauth_callback_get(monkeypatch, tmp_path):
    loaded = _load_app(monkeypatch, tmp_path, maintenance=True)
    try:
        _assert_maintenance_response(loaded.client.get("/auth/google/callback?code=test"))
    finally:
        loaded.client.close()


def test_maintenance_mode_blocks_queue_enqueue_before_redis(monkeypatch):
    monkeypatch.setenv("CLIPFORGE_MAINTENANCE_MODE", "true")
    _clear_modules()
    task_queue = importlib.import_module("task_queue")
    service = importlib.import_module("clipforge_v3.services.maintenance_service")
    monkeypatch.setattr(task_queue, "get_queue", lambda: (_ for _ in ()).throw(AssertionError("queue should not be touched")))
    with pytest.raises(service.MaintenanceModeError):
        task_queue.enqueue_video_job(123)


def test_maintenance_mode_blocks_worker_before_task_execution(monkeypatch):
    monkeypatch.setenv("CLIPFORGE_MAINTENANCE_MODE", "true")
    _clear_modules()
    task_queue = importlib.import_module("task_queue")
    service = importlib.import_module("clipforge_v3.services.maintenance_service")
    with pytest.raises(service.MaintenanceModeError):
        task_queue.run_v3_generation_wrapper(99)


def test_maintenance_mode_blocks_generation_before_take_or_cost_write(monkeypatch):
    monkeypatch.setenv("CLIPFORGE_MAINTENANCE_MODE", "true")
    _clear_modules()
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    service = importlib.import_module("clipforge_v3.services.maintenance_service")
    monkeypatch.setattr(generation, "preflight", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("preflight should not run")))
    with pytest.raises(service.MaintenanceModeError):
        generation.submit_generation(1, 1, 1, "draft")


def test_maintenance_mode_blocks_worker_provider_submit(monkeypatch):
    monkeypatch.setenv("CLIPFORGE_MAINTENANCE_MODE", "true")
    _clear_modules()
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    service = importlib.import_module("clipforge_v3.services.maintenance_service")
    monkeypatch.setattr(
        generation.take_repository,
        "get_generation_submission",
        lambda submission_id: (_ for _ in ()).throw(AssertionError("submission should not be read")),
    )
    with pytest.raises(service.MaintenanceModeError):
        generation.process_generation_submission(1)
