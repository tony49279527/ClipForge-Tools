from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
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
