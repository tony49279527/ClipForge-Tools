from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]


def _clear_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name == "db" or name == "video_core" or name == "task_queue" or name.startswith("clipforge_v3"):
            sys.modules.pop(name, None)


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    outputs_dir = tmp_path / "outputs"
    uploads_dir = tmp_path / "uploads"
    db_path = data_dir / "clipforge.db"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_URL", raising=False)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("OUTPUTS_DIR", str(outputs_dir))
    monkeypatch.setenv("UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setenv("YOUTUBE_ACCOUNTS_DIR", str(tmp_path / "secrets" / "youtube_accounts"))
    monkeypatch.setenv("CLIPFORGE_V3_ENABLED", "true")
    monkeypatch.chdir(REPO_ROOT)
    _clear_modules()
    app_module = importlib.import_module("app")
    task_queue = importlib.import_module("task_queue")
    task_queue.enqueue_storyboard_prompts_job = lambda job_id: {"job_id": job_id}
    task_queue.enqueue_video_job = lambda job_id: {"job_id": job_id}
    return SimpleNamespace(app_module=app_module, task_queue=task_queue, db_path=db_path)


@pytest.fixture
def client(app_env):
    with TestClient(app_env.app_module.app) as test_client:
        yield test_client


@pytest.fixture
def db_conn(app_env):
    app_env.db_path.parent.mkdir(parents=True, exist_ok=True)
    app_env.app_module.on_startup()
    conn = sqlite3.connect(app_env.db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def buffing_wheel_payload():
    fixture_path = REPO_ROOT / "tests" / "fixtures" / "buffing_wheel.json"
    return json.loads(fixture_path.read_text(encoding="utf-8"))


@pytest.fixture
def sample_image_file(tmp_path):
    image_path = tmp_path / "sample_identity.png"
    Image.new("RGB", (1600, 1600), color=(240, 236, 220)).save(image_path, format="PNG")
    return image_path
