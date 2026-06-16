from __future__ import annotations

import importlib
import requests


def _create_project(client, payload: dict) -> int:
    response = client.post("/v3/projects", data={k: str(v) for k, v in payload.items()}, follow_redirects=False)
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[-1])


def _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file) -> tuple[int, dict, dict]:
    project_id = _create_project(client, buffing_wheel_payload)
    client.post(f"/v3/projects/{project_id}/product-truth/confirm")
    with sample_image_file.open("rb") as handle:
        client.post(
            f"/v3/projects/{project_id}/assets",
            data={
                "primary_role": "product_identity",
                "must_transfer": "overall geometry,center hole,concentric stitched rings,natural off-white color",
                "must_not_transfer": "background,lighting,camera angle",
                "is_identity_anchor": "true",
                "user_approved": "true",
            },
            files={"asset_file": ("identity.png", handle, "image/png")},
        )
    client.post(f"/v3/projects/{project_id}/director-plan/generate")
    client.post(f"/v3/projects/{project_id}/shots/confirm-all")
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    shot = dict(db_conn.execute("SELECT * FROM v3_shots WHERE project_id = ? ORDER BY sequence_index LIMIT 1", (project_id,)).fetchone())
    prompt = generation.compile_prompt(project_id=project_id, shot_id=shot["id"])
    generation.lock_prompt(prompt["id"])
    return project_id, shot, prompt


class FakeArkProvider:
    def __init__(self, timeout_submit: bool = False):
        self.timeout_submit = timeout_submit
        self.submit_calls = 0
        self.status_calls = 0

    def validate_capabilities(self, *, mode, reference_roles):
        return {"supported": True, "unsupported": [], "missing": []}

    def build_payload(self, **kwargs):
        return kwargs

    def submit_task(self, payload):
        self.submit_calls += 1
        assert payload["resolution"]
        if self.timeout_submit:
            raise requests.Timeout("submit timed out")
        return {"request": payload, "response": {"task_id": "ark-task-123", "status": "submitted"}}

    def get_task_status(self, task_id):
        self.status_calls += 1
        return {"task_id": task_id, "status": "succeeded", "content": {"video_url": "https://cdn.example/video.mp4"}}

    def cancel_task(self, task_id):
        return {"task_id": task_id, "cancelled": True}

    def extract_result(self, response):
        return {
            "task_id": response.get("task_id"),
            "status": response.get("status"),
            "video_url": (response.get("content") or {}).get("video_url"),
            "usage": {},
            "raw": response,
        }

    def estimate_cost(self, *, duration, resolution):
        return 1.23

    def normalize_error(self, error):
        return {"code": "timeout" if isinstance(error, requests.Timeout) else "provider_error", "message": str(error)}


def test_paid_confirmation_required(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    project_id, shot, prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "ark")
    monkeypatch.setenv("V3_REAL_API_ENABLED", "true")
    try:
        generation.submit_generation(project_id, shot["id"], prompt["id"], "draft")
        assert False, "expected paid confirmation failure"
    except ValueError as exc:
        assert "Paid generation confirmation" in str(exc)


def test_idempotency_duplicate_click_reuses_submission(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    fake = FakeArkProvider()
    project_id, shot, prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "ark")
    monkeypatch.setenv("V3_REAL_API_ENABLED", "true")
    monkeypatch.setenv("V3_SYNC_PROVIDER_TASK_FOR_TESTS", "true")
    confirmation = generation.build_paid_confirmation(project_id=project_id, shot_id=shot["id"], prompt_version_id=prompt["id"], tier="draft")
    monkeypatch.setattr(generation, "get_provider", lambda: fake)
    monkeypatch.setattr(generation, "_download_provider_video", lambda video_url, project_id, shot, take_number: generation._build_mock_video(project_id, shot, take_number))
    first = generation.submit_generation(project_id, shot["id"], prompt["id"], "draft", paid_confirmed=True, paid_confirmation_token=confirmation["idempotency_key_prefix"])
    second = generation.submit_generation(project_id, shot["id"], prompt["id"], "draft", paid_confirmed=True, paid_confirmation_token=confirmation["idempotency_key_prefix"])
    assert first["take_id"] == second["take_id"]
    assert fake.submit_calls == 1
    count = db_conn.execute("SELECT COUNT(*) AS count FROM v3_generation_submissions").fetchone()["count"]
    assert count == 1


def test_timeout_enters_unknown_submission_state(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    fake = FakeArkProvider(timeout_submit=True)
    project_id, shot, prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "ark")
    monkeypatch.setenv("V3_REAL_API_ENABLED", "true")
    monkeypatch.setenv("V3_SYNC_PROVIDER_TASK_FOR_TESTS", "true")
    confirmation = generation.build_paid_confirmation(project_id=project_id, shot_id=shot["id"], prompt_version_id=prompt["id"], tier="draft")
    monkeypatch.setattr(generation, "get_provider", lambda: fake)
    result = generation.submit_generation(project_id, shot["id"], prompt["id"], "draft", paid_confirmed=True, paid_confirmation_token=confirmation["idempotency_key_prefix"])
    assert result["task_result"]["status"] == "unknown_submission_state"
    row = db_conn.execute("SELECT submission_status, provider_task_id FROM v3_generation_submissions").fetchone()
    assert row["submission_status"] == "unknown_submission_state"
    assert row["provider_task_id"] is None
    assert fake.submit_calls == 1


def test_worker_retry_with_saved_task_id_only_polls(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    fake = FakeArkProvider()
    project_id, shot, prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    confirmation = generation.build_paid_confirmation(project_id=project_id, shot_id=shot["id"], prompt_version_id=prompt["id"], tier="draft")
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "ark")
    monkeypatch.setenv("V3_REAL_API_ENABLED", "true")
    monkeypatch.setattr(generation, "get_provider", lambda: fake)
    monkeypatch.setattr(generation, "_download_provider_video", lambda video_url, project_id, shot, take_number: generation._build_mock_video(project_id, shot, take_number))
    prompt_row = generation._decode_prompt_version(dict(importlib.import_module("clipforge_v3.repositories.shot_repository").get_prompt_version(prompt["id"])))
    key = generation.build_idempotency_key(project_id=project_id, shot_id=shot["id"], prompt_version_id=prompt["id"], tier="draft", provider="ark", prompt_version=prompt_row)
    repo = importlib.import_module("clipforge_v3.repositories.take_repository")
    submission, _ = repo.reserve_generation_submission(
        {
            "project_id": project_id,
            "shot_id": shot["id"],
            "prompt_version_id": prompt["id"],
            "generation_tier": "draft",
            "provider": "ark",
            "idempotency_key": key,
            "provider_request_hash": "hash",
            "submission_status": "submitted",
            "paid_confirmed": True,
            "confirmation_token": confirmation["idempotency_key_prefix"],
            "request_payload_json": prompt_row["provider_payload_json"],
        }
    )
    repo.update_generation_submission(submission["id"], {"provider_task_id": "ark-task-existing"})
    result = generation.process_generation_submission(submission["id"])
    assert result["status"] == "succeeded"
    assert fake.submit_calls == 0
    assert fake.status_calls >= 1


def test_secret_not_in_submission_payload(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    fake = FakeArkProvider()
    project_id, shot, prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    monkeypatch.setenv("ARK_API_KEY", "super-secret-key")
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "ark")
    monkeypatch.setenv("V3_REAL_API_ENABLED", "true")
    monkeypatch.setenv("V3_SYNC_PROVIDER_TASK_FOR_TESTS", "true")
    confirmation = generation.build_paid_confirmation(project_id=project_id, shot_id=shot["id"], prompt_version_id=prompt["id"], tier="draft")
    monkeypatch.setattr(generation, "get_provider", lambda: fake)
    monkeypatch.setattr(generation, "_download_provider_video", lambda video_url, project_id, shot, take_number: generation._build_mock_video(project_id, shot, take_number))
    generation.submit_generation(project_id, shot["id"], prompt["id"], "draft", paid_confirmed=True, paid_confirmation_token=confirmation["idempotency_key_prefix"])
    raw = db_conn.execute("SELECT request_payload_json, response_json FROM v3_generation_submissions").fetchone()
    assert "super-secret-key" not in raw["request_payload_json"]
    assert "super-secret-key" not in raw["response_json"]


def test_mock_mode_never_submits_even_if_ark_key_exists(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    project_id, shot, prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    monkeypatch.setenv("ARK_API_KEY", "super-secret-key")
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "mock")
    monkeypatch.setenv("V3_REAL_API_ENABLED", "false")

    class ExplodingProvider(FakeArkProvider):
        def submit_task(self, payload):
            raise AssertionError("mock mode must not call Ark submit_task")

    monkeypatch.setattr(generation, "get_provider", lambda: ExplodingProvider())
    result = generation.submit_generation(project_id, shot["id"], prompt["id"], "draft")
    assert result["take_id"]
    row = db_conn.execute("SELECT provider FROM v3_usage_events WHERE take_id = ?", (result["take_id"],)).fetchone()
    assert row["provider"] == "mock"
