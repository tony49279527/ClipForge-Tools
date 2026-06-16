from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import sqlite3
from unittest.mock import Mock
import requests
import pytest


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


def _set_asset_url(db_conn, *, project_id: int, remote_url: str | None = None, access_url: str | None = None) -> None:
    row = db_conn.execute("SELECT id FROM v3_assets WHERE project_id = ? ORDER BY id LIMIT 1", (project_id,)).fetchone()
    assert row
    asset_repo = importlib.import_module("clipforge_v3.repositories.asset_repository")
    fields = {}
    if remote_url is not None:
        fields["remote_url"] = remote_url
    if access_url is not None:
        fields["access_url"] = access_url
    asset_repo.update_asset(row["id"], fields)


def _compile_prompt_with_https_asset(generation, db_conn, *, project_id: int, shot: dict) -> dict:
    _set_asset_url(db_conn, project_id=project_id, remote_url=f"https://cdn.example.com/products/{project_id}/identity.jpg")
    prompt = generation.compile_prompt(project_id=project_id, shot_id=shot["id"])
    generation.lock_prompt(prompt["id"])
    return prompt


class FakeArkProvider:
    def __init__(self, timeout_submit: bool = False, video_url: str = "https://cdn.example/video.mp4"):
        self.timeout_submit = timeout_submit
        self.video_url = video_url
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
        return {"task_id": task_id, "status": "succeeded", "content": {"video_url": self.video_url}}

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


def _content_image_urls(payload: dict) -> list[str]:
    urls = []
    for entry in payload.get("content", []):
        if entry.get("type") == "image_url":
            urls.append(entry.get("image_url", {}).get("url"))
    return urls


def _load_inspector_module():
    spec = importlib.util.spec_from_file_location("inspect_real_seedance_payload", Path("scripts/v3/inspect_real_seedance_payload.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _ark_confirmation(generation, *, project_id: int, shot: dict, prompt: dict) -> dict:
    return generation.build_paid_confirmation(project_id=project_id, shot_id=shot["id"], prompt_version_id=prompt["id"], tier="draft")


def _patch_successful_ark(generation, monkeypatch, fake: FakeArkProvider) -> None:
    monkeypatch.setattr(generation, "get_provider", lambda: fake)
    monkeypatch.setattr(generation, "_download_provider_video", lambda video_url, project_id, shot, take_number: generation._build_mock_video(project_id, shot, take_number))


def _count_rows(db_conn, table: str) -> int:
    return db_conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]


def _usage_count_for_submission(db_conn, submission_id: int) -> int:
    return db_conn.execute(
        "SELECT COUNT(*) AS count FROM v3_usage_events WHERE event_key = ?",
        (f"provider_generation:{submission_id}",),
    ).fetchone()["count"]


def _submit_successful_real_generation(generation, db_conn, monkeypatch, *, client, buffing_wheel_payload, sample_image_file, fake=None):
    fake = fake or FakeArkProvider()
    project_id, shot, _prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    prompt = _compile_prompt_with_https_asset(generation, db_conn, project_id=project_id, shot=shot)
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "ark")
    monkeypatch.setenv("V3_REAL_API_ENABLED", "true")
    monkeypatch.setenv("V3_SYNC_PROVIDER_TASK_FOR_TESTS", "true")
    confirmation = _ark_confirmation(generation, project_id=project_id, shot=shot, prompt=prompt)
    _patch_successful_ark(generation, monkeypatch, fake)
    result = generation.submit_generation(project_id, shot["id"], prompt["id"], "draft", paid_confirmed=True, paid_confirmation_token=confirmation["confirmation_token"])
    submission = db_conn.execute("SELECT * FROM v3_generation_submissions").fetchone()
    return project_id, shot, prompt, fake, result, submission


def _reserve_existing_provider_submission(generation, *, project_id: int, shot: dict, prompt: dict, status: str = "downloading") -> dict:
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
            "submission_status": status,
            "paid_confirmed": True,
            "confirmation_token": key[:12],
            "request_payload_json": prompt_row["provider_payload_json"],
            "budget_approved_at": "2026-06-16T00:00:00Z",
        }
    )
    repo.update_generation_submission(submission["id"], {"provider_task_id": "ark-task-existing"})
    return repo.get_generation_submission(submission["id"])


def test_paid_confirmation_required(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    project_id, shot, _prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    prompt = _compile_prompt_with_https_asset(generation, db_conn, project_id=project_id, shot=shot)
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "ark")
    monkeypatch.setenv("V3_REAL_API_ENABLED", "true")
    try:
        generation.submit_generation(project_id, shot["id"], prompt["id"], "draft")
        assert False, "expected paid confirmation failure"
    except ValueError as exc:
        assert "Paid generation confirmation" in str(exc)


def test_paid_confirmation_contract_contains_flat_fields(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    project_id, shot, prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "ark")
    confirmation = generation.build_paid_confirmation(project_id=project_id, shot_id=shot["id"], prompt_version_id=prompt["id"], tier="draft")
    assert confirmation["project_id"] == project_id
    assert confirmation["shot_db_id"] == shot["id"]
    assert confirmation["shot_id"] == shot["shot_id"]
    assert confirmation["confirmation_token"]
    assert confirmation["confirmation_token"] == confirmation["idempotency_key_prefix"]


def test_wrong_paid_confirmation_token_is_rejected(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    project_id, shot, _prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    prompt = _compile_prompt_with_https_asset(generation, db_conn, project_id=project_id, shot=shot)
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "ark")
    monkeypatch.setenv("V3_REAL_API_ENABLED", "true")
    try:
        generation.submit_generation(project_id, shot["id"], prompt["id"], "draft", paid_confirmed=True, paid_confirmation_token="wrong-token")
        assert False, "expected paid confirmation token rejection"
    except ValueError as exc:
        assert "Paid generation confirmation" in str(exc)


def test_idempotency_duplicate_click_reuses_submission(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    fake = FakeArkProvider()
    project_id, shot, _prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    prompt = _compile_prompt_with_https_asset(generation, db_conn, project_id=project_id, shot=shot)
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "ark")
    monkeypatch.setenv("V3_REAL_API_ENABLED", "true")
    monkeypatch.setenv("V3_SYNC_PROVIDER_TASK_FOR_TESTS", "true")
    confirmation = generation.build_paid_confirmation(project_id=project_id, shot_id=shot["id"], prompt_version_id=prompt["id"], tier="draft")
    monkeypatch.setattr(generation, "get_provider", lambda: fake)
    monkeypatch.setattr(generation, "_download_provider_video", lambda video_url, project_id, shot, take_number: generation._build_mock_video(project_id, shot, take_number))
    first = generation.submit_generation(project_id, shot["id"], prompt["id"], "draft", paid_confirmed=True, paid_confirmation_token=confirmation["confirmation_token"])
    second = generation.submit_generation(project_id, shot["id"], prompt["id"], "draft", paid_confirmed=True, paid_confirmation_token=confirmation["confirmation_token"])
    assert first["take_id"] == second["take_id"]
    assert fake.submit_calls == 1
    count = db_conn.execute("SELECT COUNT(*) AS count FROM v3_generation_submissions").fetchone()["count"]
    assert count == 1


def test_https_remote_url_enters_ark_payload(client, db_conn, buffing_wheel_payload, sample_image_file):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    project_id, shot, _prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    image_url = "https://cdn.example.com/products/buffing-wheel.jpg?signature=secret"
    _set_asset_url(db_conn, project_id=project_id, remote_url=image_url)
    prompt = generation.compile_prompt(project_id=project_id, shot_id=shot["id"])
    urls = _content_image_urls(prompt["provider_payload_json"])
    assert image_url in urls
    image_entries = [entry for entry in prompt["provider_payload_json"]["content"] if entry.get("type") == "image_url"]
    assert image_entries[0]["reference_role"] == "product_identity"
    assert image_entries[0]["label"] == "Image1"
    assert image_entries[0]["must_transfer"]


def test_https_access_url_fallback_enters_ark_payload(client, db_conn, buffing_wheel_payload, sample_image_file):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    project_id, shot, _prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    image_url = "https://assets.example.net/public/product-side.png"
    _set_asset_url(db_conn, project_id=project_id, access_url=image_url)
    prompt = generation.compile_prompt(project_id=project_id, shot_id=shot["id"])
    assert image_url in _content_image_urls(prompt["provider_payload_json"])


def test_ark_submit_payload_contains_real_https_image_url(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    seedance = importlib.import_module("clipforge_v3.providers.seedance_ark")
    project_id, shot, _prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    image_url = "https://cdn.example.com/products/buffing-wheel.jpg?signature=secret"
    _set_asset_url(db_conn, project_id=project_id, remote_url=image_url)
    prompt = generation.compile_prompt(project_id=project_id, shot_id=shot["id"])
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"task_id": "ark-task-url", "status": "submitted"}

    def fake_post(url, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return Response()

    monkeypatch.setenv("ARK_API_KEY", "super-secret-key")
    monkeypatch.setattr(seedance.requests, "post", fake_post)
    result = seedance.ArkSeedanceProvider().submit_task(prompt["provider_payload_json"])
    assert image_url in _content_image_urls(captured["json"])
    assert captured["headers"]["Authorization"] == "Bearer super-secret-key"
    assert "super-secret-key" not in str(result)
    assert "signature=secret" not in str(result["request"])


def test_ark_preflight_blocks_identity_asset_without_https_source(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    project_id, shot, prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "ark")
    result = generation.preflight(project_id, shot["id"], prompt["id"], "draft")
    assert not result["allow_submit"]
    assert any(item["name"] == "provider_asset_source_product_identity" and not item["passed"] for item in result["items"])
    assert "MISSING_PROVIDER_ASSET_SOURCE" in str(result)


def test_provider_asset_resolver_rejects_non_public_https_sources():
    resolver = importlib.import_module("clipforge_v3.services.provider_asset_resolver")
    role = {"asset_id": 1, "primary_role": "product_identity", "must_transfer": [], "must_not_transfer": []}
    cases = [
        ("http://example.com/product.jpg", "not_https"),
        ("https://localhost/product.jpg", "blocked_host"),
        ("https://127.0.0.1/product.jpg", "blocked_host"),
        ("https://10.0.0.5/product.jpg", "blocked_host"),
        ("file:///tmp/product.jpg", "not_https"),
        ("", "empty_url"),
        ("not a url", "not_https"),
    ]
    for url, reason in cases:
        ref = resolver.resolve_provider_reference({"id": 1, "remote_url": url, "primary_role": "product_identity"}, role, label="Image1")
        assert not ref["available"]
        assert ref["url"] is None
        assert ref["unavailable_reason"] == reason


def test_mock_mode_still_allows_local_identity_asset(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    project_id, shot, prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "mock")
    result = generation.submit_generation(project_id, shot["id"], prompt["id"], "draft")
    assert result["take_id"]


def test_timeout_enters_unknown_submission_state(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    fake = FakeArkProvider(timeout_submit=True)
    project_id, shot, _prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    prompt = _compile_prompt_with_https_asset(generation, db_conn, project_id=project_id, shot=shot)
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "ark")
    monkeypatch.setenv("V3_REAL_API_ENABLED", "true")
    monkeypatch.setenv("V3_SYNC_PROVIDER_TASK_FOR_TESTS", "true")
    confirmation = generation.build_paid_confirmation(project_id=project_id, shot_id=shot["id"], prompt_version_id=prompt["id"], tier="draft")
    monkeypatch.setattr(generation, "get_provider", lambda: fake)
    result = generation.submit_generation(project_id, shot["id"], prompt["id"], "draft", paid_confirmed=True, paid_confirmation_token=confirmation["confirmation_token"])
    assert result["task_result"]["status"] == "unknown_submission_state"
    row = db_conn.execute("SELECT submission_status, provider_task_id FROM v3_generation_submissions").fetchone()
    assert row["submission_status"] == "unknown_submission_state"
    assert row["provider_task_id"] is None
    assert fake.submit_calls == 1
    retry = generation.submit_generation(project_id, shot["id"], prompt["id"], "draft", paid_confirmed=True, paid_confirmation_token=confirmation["confirmation_token"])
    assert retry["task_result"]["manual_reconciliation_required"] is True
    assert retry["task_result"]["auto_retry_disabled"] is True
    assert fake.submit_calls == 1
    submission_id = db_conn.execute("SELECT id FROM v3_generation_submissions").fetchone()["id"]
    worker_retry = generation.process_generation_submission(submission_id)
    assert worker_retry["manual_reconciliation_required"] is True
    assert fake.submit_calls == 1


def test_worker_retry_with_saved_task_id_only_polls(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    fake = FakeArkProvider()
    project_id, shot, _prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    prompt = _compile_prompt_with_https_asset(generation, db_conn, project_id=project_id, shot=shot)
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
            "confirmation_token": confirmation["confirmation_token"],
            "request_payload_json": prompt_row["provider_payload_json"],
        }
    )
    repo.update_generation_submission(submission["id"], {"provider_task_id": "ark-task-existing"})
    result = generation.process_generation_submission(submission["id"])
    assert result["status"] == "succeeded"
    assert fake.submit_calls == 0
    assert fake.status_calls >= 1
    assert _count_rows(db_conn, "v3_takes") == 1
    assert _usage_count_for_submission(db_conn, submission["id"]) == 1


def test_unknown_submission_without_task_id_worker_retry_does_not_submit(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    fake = FakeArkProvider()
    project_id, shot, _prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    prompt = _compile_prompt_with_https_asset(generation, db_conn, project_id=project_id, shot=shot)
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
            "submission_status": "unknown_submission_state",
            "paid_confirmed": True,
            "confirmation_token": key[:12],
            "request_payload_json": prompt_row["provider_payload_json"],
            "budget_approved_at": "2026-06-16T00:00:00Z",
            "error_json": {"possible_charge": True},
        }
    )
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "ark")
    monkeypatch.setenv("V3_REAL_API_ENABLED", "true")
    monkeypatch.setattr(generation, "get_provider", lambda: fake)
    result = generation.process_generation_submission(submission["id"])
    assert result["manual_reconciliation_required"] is True
    assert fake.submit_calls == 0


def test_budget_failure_does_not_create_submit_ready_reservation(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    fake = FakeArkProvider()
    project_id, shot, _prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    prompt = _compile_prompt_with_https_asset(generation, db_conn, project_id=project_id, shot=shot)
    importlib.import_module("clipforge_v3.repositories.shot_repository").update_shot(shot["id"], {"max_draft_takes": 1})
    importlib.import_module("clipforge_v3.repositories.take_repository").create_take(
        {
            "shot_id": shot["id"],
            "take_number": 1,
            "prompt_version_id": prompt["id"],
            "status": "completed",
            "local_path": "existing.mp4",
            "generation_settings_json": {},
            "tier": "draft",
        }
    )
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "ark")
    monkeypatch.setenv("V3_REAL_API_ENABLED", "true")
    monkeypatch.setenv("V3_SYNC_PROVIDER_TASK_FOR_TESTS", "true")
    monkeypatch.setattr(generation, "get_provider", lambda: fake)
    confirmation = _ark_confirmation(generation, project_id=project_id, shot=shot, prompt=prompt)
    with pytest.raises(ValueError, match="take budget exceeded"):
        generation.submit_generation(project_id, shot["id"], prompt["id"], "draft", paid_confirmed=True, paid_confirmation_token=confirmation["confirmation_token"])
    assert _count_rows(db_conn, "v3_generation_submissions") == 0
    assert fake.submit_calls == 0


def test_old_reserved_row_without_budget_approval_cannot_submit(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    fake = FakeArkProvider()
    project_id, shot, _prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    prompt = _compile_prompt_with_https_asset(generation, db_conn, project_id=project_id, shot=shot)
    prompt_row = generation._decode_prompt_version(dict(importlib.import_module("clipforge_v3.repositories.shot_repository").get_prompt_version(prompt["id"])))
    key = generation.build_idempotency_key(project_id=project_id, shot_id=shot["id"], prompt_version_id=prompt["id"], tier="draft", provider="ark", prompt_version=prompt_row)
    repo = importlib.import_module("clipforge_v3.repositories.take_repository")
    repo.reserve_generation_submission(
        {
            "project_id": project_id,
            "shot_id": shot["id"],
            "prompt_version_id": prompt["id"],
            "generation_tier": "draft",
            "provider": "ark",
            "idempotency_key": key,
            "provider_request_hash": "hash",
            "submission_status": "reserved",
            "paid_confirmed": True,
            "confirmation_token": key[:12],
            "request_payload_json": prompt_row["provider_payload_json"],
        }
    )
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "ark")
    monkeypatch.setenv("V3_REAL_API_ENABLED", "true")
    monkeypatch.setenv("V3_SYNC_PROVIDER_TASK_FOR_TESTS", "true")
    monkeypatch.setattr(generation, "get_provider", lambda: fake)
    result = generation.submit_generation(project_id, shot["id"], prompt["id"], "draft", paid_confirmed=True, paid_confirmation_token=key[:12])
    assert result["task_result"]["reason"] == "budget_approval_required"
    assert fake.submit_calls == 0


def test_ark_status_preserves_signed_video_url_query(monkeypatch):
    seedance = importlib.import_module("clipforge_v3.providers.seedance_ark")
    signed_url = "https://cdn.example/video.mp4?X-Amz-Signature=a%2Bb&Expires=1&token=abc%3Ddef"

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id": "ark-task-existing", "status": "succeeded", "content": {"video_url": signed_url}}

    monkeypatch.setenv("ARK_API_KEY", "secret")
    monkeypatch.setattr(seedance.requests, "get", lambda *args, **kwargs: Response())
    result = seedance.ArkSeedanceProvider().get_task_status("ark-task-existing")
    assert result["content"]["video_url"] == signed_url
    extracted = seedance.ArkSeedanceProvider().extract_result(result)
    assert extracted["video_url"] == signed_url
    assert extracted["raw"]["content"]["video_url"] == "https://cdn.example/video.mp4"


def test_download_uses_full_signed_url_and_allows_redirects(tmp_path, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    signed_url = "https://cdn.example/video.mp4?X-Amz-Signature=a%2Bb&Expires=1&token=abc%3Ddef"
    requested = {}

    class Redirect:
        status_code = 302
        url = "https://ark.example/redirect?token=first"

    class Response:
        status_code = 200
        url = "https://cdn.example/final.mp4?X-Amz-Signature=final%2Bsig"
        headers = {"content-type": "video/mp4"}
        history = [Redirect()]

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            yield b"video"

    def fake_get(url, timeout, stream):
        requested["url"] = url
        requested["timeout"] = timeout
        requested["stream"] = stream
        return Response()

    monkeypatch.setattr(generation, "OUTPUTS_DIR", tmp_path)
    monkeypatch.setattr(generation.requests, "get", fake_get)
    monkeypatch.setattr(generation, "_extract_frames", lambda path: [str(path.with_name("first.jpg")), str(path.with_name("last.jpg"))])
    local_path, qc_paths = generation._download_provider_video(signed_url, 1, {"shot_id": "S01"}, 1)
    assert requested == {"url": signed_url, "timeout": (10, 180), "stream": True}
    assert Path(local_path).read_bytes() == b"video"
    assert len(qc_paths) == 2


@pytest.mark.parametrize("initial_status", ["downloading", "download_failed"])
def test_existing_provider_task_recovery_download_failure_does_not_submit_or_duplicate(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch, initial_status):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    signed_url = "https://cdn.example/video.mp4?X-Amz-Signature=a%2Bb&Expires=1&token=abc%3Ddef"
    fake = FakeArkProvider(video_url=signed_url)
    fake.submit_task = Mock(side_effect=AssertionError("recovery must not create a provider task"))
    project_id, shot, _prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    prompt = _compile_prompt_with_https_asset(generation, db_conn, project_id=project_id, shot=shot)
    submission = _reserve_existing_provider_submission(generation, project_id=project_id, shot=shot, prompt=prompt, status=initial_status)
    response = requests.Response()
    response.status_code = 403
    response.url = signed_url
    response.headers["content-type"] = "application/xml"
    response.headers["content-length"] = "123"
    error = requests.HTTPError(f"403 Client Error: Forbidden for url: {signed_url}", response=response)
    captured = {}

    def fail_download(video_url, project_id, shot, take_number):
        captured["video_url"] = video_url
        raise error

    monkeypatch.setattr(generation, "get_provider", lambda: fake)
    monkeypatch.setattr(generation, "_download_provider_video", fail_download)
    result = generation.recover_generation_submission(submission["id"])
    fake.submit_task.assert_not_called()
    assert fake.status_calls == 1
    assert captured["video_url"] == signed_url
    assert result["status"] == "download_recovery_required"
    row = db_conn.execute("SELECT submission_status, provider_task_id, take_id, error_json FROM v3_generation_submissions WHERE id = ?", (submission["id"],)).fetchone()
    assert row["submission_status"] == "download_failed"
    assert row["provider_task_id"] == "ark-task-existing"
    assert row["take_id"] is None
    assert "token=abc" not in row["error_json"]
    assert "X-Amz-Signature" in row["error_json"]
    assert _count_rows(db_conn, "v3_generation_submissions") == 1
    assert _count_rows(db_conn, "v3_takes") == 0
    assert _usage_count_for_submission(db_conn, submission["id"]) == 1


@pytest.mark.parametrize("initial_status", ["downloading", "download_failed"])
def test_existing_provider_task_recovery_success_is_idempotent(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch, initial_status):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    signed_url = "https://cdn.example/video.mp4?X-Amz-Signature=a%2Bb&Expires=1&token=abc%3Ddef"
    fake = FakeArkProvider(video_url=signed_url)
    fake.submit_task = Mock(side_effect=AssertionError("recovery must not create a provider task"))
    project_id, shot, _prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    prompt = _compile_prompt_with_https_asset(generation, db_conn, project_id=project_id, shot=shot)
    submission = _reserve_existing_provider_submission(generation, project_id=project_id, shot=shot, prompt=prompt, status=initial_status)
    captured = []

    def successful_download(video_url, project_id, shot, take_number):
        captured.append(video_url)
        return generation._build_mock_video(project_id, shot, take_number)

    monkeypatch.setattr(generation, "get_provider", lambda: fake)
    monkeypatch.setattr(generation, "_download_provider_video", successful_download)
    first = generation.recover_generation_submission(submission["id"])
    second = generation.recover_generation_submission(submission["id"])
    fake.submit_task.assert_not_called()
    assert fake.status_calls == 2
    assert captured == [signed_url]
    assert first["status"] == "succeeded"
    assert second["status"] == "succeeded"
    row = db_conn.execute("SELECT submission_status, provider_task_id, take_id FROM v3_generation_submissions WHERE id = ?", (submission["id"],)).fetchone()
    assert row["submission_status"] == "succeeded"
    assert row["provider_task_id"] == "ark-task-existing"
    assert row["take_id"] is not None
    assert _count_rows(db_conn, "v3_generation_submissions") == 1
    assert _count_rows(db_conn, "v3_takes") == 1
    assert _usage_count_for_submission(db_conn, submission["id"]) == 1
    usage = db_conn.execute("SELECT take_id FROM v3_usage_events WHERE event_key = ?", (f"provider_generation:{submission['id']}",)).fetchone()
    assert usage["take_id"] == row["take_id"]


@pytest.mark.parametrize(
    "crash_point",
    ["after_download_before_take", "after_take_before_submission_link", "after_submission_link_before_usage", "after_usage_before_shot_update"],
)
def test_provider_success_replay_is_idempotent_after_worker_crash(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch, crash_point):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    repo = importlib.import_module("clipforge_v3.repositories.take_repository")
    project_repo = importlib.import_module("clipforge_v3.repositories.project_repository")
    shot_repo = importlib.import_module("clipforge_v3.repositories.shot_repository")
    fake = FakeArkProvider()
    project_id, shot, _prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    prompt = _compile_prompt_with_https_asset(generation, db_conn, project_id=project_id, shot=shot)
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "ark")
    monkeypatch.setenv("V3_REAL_API_ENABLED", "true")
    monkeypatch.setenv("V3_SYNC_PROVIDER_TASK_FOR_TESTS", "true")
    confirmation = _ark_confirmation(generation, project_id=project_id, shot=shot, prompt=prompt)
    _patch_successful_ark(generation, monkeypatch, fake)
    crash_state = {"raised": False}
    original_create_take = repo.create_take
    original_update_submission = repo.update_generation_submission
    original_create_usage = project_repo.create_usage_event
    original_update_shot = shot_repo.update_shot

    def maybe_crash_after_download(payload):
        if crash_point == "after_download_before_take" and payload.get("generation_submission_id") and not crash_state["raised"]:
            crash_state["raised"] = True
            raise RuntimeError("simulated crash after download")
        return original_create_take(payload)

    def maybe_crash_after_take(submission_id, fields):
        if crash_point == "after_take_before_submission_link" and fields.get("take_id") and not crash_state["raised"]:
            crash_state["raised"] = True
            raise RuntimeError("simulated crash after take")
        return original_update_submission(submission_id, fields)

    def maybe_crash_after_submission_link(payload):
        if crash_point == "after_submission_link_before_usage" and payload.get("event_key", "").startswith("provider_generation:") and not crash_state["raised"]:
            crash_state["raised"] = True
            raise RuntimeError("simulated crash after submission link")
        return original_create_usage(payload)

    def maybe_crash_after_usage(shot_id, fields):
        if crash_point == "after_usage_before_shot_update" and fields.get("status") == "generated" and not crash_state["raised"]:
            crash_state["raised"] = True
            raise RuntimeError("simulated crash after usage")
        return original_update_shot(shot_id, fields)

    monkeypatch.setattr(repo, "create_take", maybe_crash_after_download)
    monkeypatch.setattr(repo, "update_generation_submission", maybe_crash_after_take)
    monkeypatch.setattr(project_repo, "create_usage_event", maybe_crash_after_submission_link)
    monkeypatch.setattr(shot_repo, "update_shot", maybe_crash_after_usage)
    with pytest.raises(RuntimeError, match="simulated crash"):
        generation.submit_generation(project_id, shot["id"], prompt["id"], "draft", paid_confirmed=True, paid_confirmation_token=confirmation["confirmation_token"])
    submission = db_conn.execute("SELECT * FROM v3_generation_submissions").fetchone()
    assert submission["provider_task_id"] == "ark-task-123"
    assert fake.submit_calls == 1
    result = generation.process_generation_submission(submission["id"])
    assert result["status"] == "succeeded"
    assert fake.submit_calls == 1
    assert _count_rows(db_conn, "v3_takes") == 1
    assert _usage_count_for_submission(db_conn, submission["id"]) == 1
    linked = db_conn.execute("SELECT take_id, submission_status FROM v3_generation_submissions WHERE id = ?", (submission["id"],)).fetchone()
    take = db_conn.execute("SELECT id, generation_submission_id FROM v3_takes").fetchone()
    assert linked["submission_status"] == "succeeded"
    assert linked["take_id"] == take["id"]
    assert take["generation_submission_id"] == submission["id"]


def test_completed_provider_submission_can_be_replayed_without_duplicate_take_or_cost(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    _, _, _, fake, _result, submission = _submit_successful_real_generation(
        generation,
        db_conn,
        monkeypatch,
        client=client,
        buffing_wheel_payload=buffing_wheel_payload,
        sample_image_file=sample_image_file,
    )
    replay = generation.process_generation_submission(submission["id"])
    assert replay["status"] == "succeeded"
    assert fake.submit_calls == 1
    assert _count_rows(db_conn, "v3_takes") == 1
    assert _usage_count_for_submission(db_conn, submission["id"]) == 1


def test_database_constraints_prevent_duplicate_take_and_usage_for_submission(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    repo = importlib.import_module("clipforge_v3.repositories.take_repository")
    project_repo = importlib.import_module("clipforge_v3.repositories.project_repository")
    _, shot, prompt, _fake, _result, submission = _submit_successful_real_generation(
        generation,
        db_conn,
        monkeypatch,
        client=client,
        buffing_wheel_payload=buffing_wheel_payload,
        sample_image_file=sample_image_file,
    )
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_take(
            {
                "shot_id": shot["id"],
                "take_number": repo.get_next_take_number(shot["id"]),
                "prompt_version_id": prompt["id"],
                "status": "completed",
                "local_path": "duplicate.mp4",
                "generation_settings_json": {},
                "tier": "draft",
                "generation_submission_id": submission["id"],
            }
        )
    existing_event_id = db_conn.execute(
        "SELECT id FROM v3_usage_events WHERE event_key = ?",
        (f"provider_generation:{submission['id']}",),
    ).fetchone()["id"]
    duplicate_event_id = project_repo.create_usage_event(
        {
            "project_id": submission["project_id"],
            "shot_id": shot["id"],
            "take_id": submission["take_id"],
            "stage": "seedance_draft",
            "provider": "ark",
            "model": prompt["provider_payload_json"]["model"],
            "duration": prompt["provider_payload_json"]["duration"],
            "resolution": prompt["provider_payload_json"]["resolution"],
            "estimated_cost": 1.23,
            "status": "succeeded",
            "raw_usage_json": {},
            "event_key": f"provider_generation:{submission['id']}",
            "source_type": "generation_submission",
            "source_id": submission["id"],
        }
    )
    assert duplicate_event_id == existing_event_id
    assert _usage_count_for_submission(db_conn, submission["id"]) == 1


def test_state_machine_hardening_migration_is_repeatable(app_env, db_conn):
    migrations = importlib.import_module("clipforge_v3.migrations")
    assert migrations.run_v3_migrations() == []
    assert migrations.run_v3_migrations() == []


def test_secret_not_in_submission_payload(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    fake = FakeArkProvider()
    project_id, shot, _prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    prompt = _compile_prompt_with_https_asset(generation, db_conn, project_id=project_id, shot=shot)
    monkeypatch.setenv("ARK_API_KEY", "super-secret-key")
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "ark")
    monkeypatch.setenv("V3_REAL_API_ENABLED", "true")
    monkeypatch.setenv("V3_SYNC_PROVIDER_TASK_FOR_TESTS", "true")
    confirmation = generation.build_paid_confirmation(project_id=project_id, shot_id=shot["id"], prompt_version_id=prompt["id"], tier="draft")
    monkeypatch.setattr(generation, "get_provider", lambda: fake)
    monkeypatch.setattr(generation, "_download_provider_video", lambda video_url, project_id, shot, take_number: generation._build_mock_video(project_id, shot, take_number))
    generation.submit_generation(project_id, shot["id"], prompt["id"], "draft", paid_confirmed=True, paid_confirmation_token=confirmation["confirmation_token"])
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


def test_inspector_accepts_public_https_url_and_builds_payload(app_env, monkeypatch):
    inspector = _load_inspector_module()
    image_url = "https://cdn.example.com/products/buffing-wheel.jpg"
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "ark")
    summary = inspector.build_payload_summary(image_url)
    assert summary["preflight_passed"] is True
    assert summary["ready_for_paid_submission"] is True
    assert summary["reference_count"] >= 1
    assert summary["reference_1_role"] == "product_identity"
    assert summary["reference_1_source_type"] == "image_url"
    assert summary["reference_1_host"] == "cdn.example.com"
    assert summary["reference_1_has_actual_image_url"] is True
    assert image_url in _content_image_urls(summary["payload"])


def test_inspector_does_not_call_provider_submission():
    source = Path("scripts/v3/inspect_real_seedance_payload.py").read_text(encoding="utf-8")
    assert "submit_task(" not in source
    assert "submit_generation(" not in source
    assert "requests.post" not in source


def test_inspector_missing_url_exits_safely(monkeypatch, capsys):
    inspector = _load_inspector_module()
    monkeypatch.delenv("V3_REAL_TEST_IMAGE_URL", raising=False)
    assert inspector.main() == 2
    assert "V3_REAL_TEST_IMAGE_URL" in capsys.readouterr().out


def test_inspector_rejects_unsafe_urls(monkeypatch, capsys):
    inspector = _load_inspector_module()
    for image_url in [
        "http://cdn.example.com/product.jpg",
        "https://localhost/product.jpg",
        "https://127.0.0.1/product.jpg",
        "https://10.1.2.3/product.jpg",
    ]:
        monkeypatch.setenv("V3_REAL_TEST_IMAGE_URL", image_url)
        assert inspector.main() == 2
        assert "invalid V3_REAL_TEST_IMAGE_URL" in capsys.readouterr().out


def test_inspector_output_redacts_api_key_and_signed_query(app_env, monkeypatch, capsys):
    inspector = _load_inspector_module()
    image_url = "https://signed.example.com/path/product.jpg?X-Amz-Signature=secret-query"
    monkeypatch.setenv("ARK_API_KEY", "super-secret-key")
    summary = inspector.build_payload_summary(image_url)
    inspector.print_summary(summary)
    output = capsys.readouterr().out
    assert "super-secret-key" not in output
    assert "X-Amz-Signature" not in output
    assert "secret-query" not in output
    assert "signed.example.com" in output
    assert image_url in _content_image_urls(summary["payload"])


def test_manual_real_seedance_script_uses_paid_confirmation_contract_fields():
    script = Path("scripts/v3/test_real_seedance_single_shot.py").read_text(encoding="utf-8")
    assert "confirmation['shot']" not in script
    assert 'confirmation["shot"]' not in script
    assert "confirmation['shot_id']" in script or 'confirmation["shot_id"]' in script
    assert "confirmation['confirmation_token']" in script or 'confirmation["confirmation_token"]' in script
    assert '"remote_url": image_url' in script
    assert "Image.new" not in script


def test_recovery_script_cannot_create_provider_submission():
    script = Path("scripts/v3/recover_real_seedance_submission.py").read_text(encoding="utf-8")
    assert "NO NEW PROVIDER SUBMISSION" in script
    assert "submit_task(" not in script
    assert "submit_generation" not in script
    assert "test_real_seedance_single_shot" not in script
    assert "YES_PAY_SEEDANCE_ONCE" not in script
