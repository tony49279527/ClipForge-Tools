from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.v3.test_real_provider_alpha import (
    FakeArkProvider,
    _compile_prompt_with_https_asset,
    _count_rows,
    _prepare_project,
    _reserve_existing_provider_submission,
    _usage_count_for_submission,
)


class FakeR2Client:
    def __init__(self, *, fail_put: bool = False):
        self.fail_put = fail_put
        self.objects: dict[tuple[str, str], dict] = {}
        self.put_calls: list[dict] = []
        self.head_calls: list[dict] = []
        self.presign_calls: list[dict] = []

    def head_object(self, *, Bucket, Key):
        self.head_calls.append({"Bucket": Bucket, "Key": Key})
        if (Bucket, Key) not in self.objects:
            raise RuntimeError("not found")
        stored = self.objects[(Bucket, Key)]
        return {"ContentLength": len(stored["body"]), "ContentType": stored.get("content_type")}

    def put_object(self, *, Bucket, Key, Body, ContentType, CacheControl):
        if self.fail_put:
            raise RuntimeError("r2 upload failed")
        body = Body.read()
        self.put_calls.append({"Bucket": Bucket, "Key": Key, "ContentType": ContentType, "CacheControl": CacheControl})
        self.objects[(Bucket, Key)] = {"body": body, "content_type": ContentType, "cache_control": CacheControl}

    def delete_object(self, *, Bucket, Key):
        self.objects.pop((Bucket, Key), None)

    def get_object(self, *, Bucket, Key):
        return {"Body": self.objects[(Bucket, Key)]["body"]}

    def generate_presigned_url(self, ClientMethod, Params, ExpiresIn):
        self.presign_calls.append({"method": ClientMethod, "params": Params, "expires": ExpiresIn})
        return f"https://r2-download.example/{Params['Key']}?X-Amz-Signature=redacted&Expires={ExpiresIn}"


def _r2_storage(fake_client: FakeR2Client, *, public_bucket: str = "clipforge-public-assets", private_bucket: str = "clipforge-private-videos", mode: str = "dual"):
    storage = importlib.import_module("clipforge_v3.services.storage_service")
    return storage.R2Storage(
        client=fake_client,
        config=storage.R2Config(
            endpoint_url="https://acct.r2.cloudflarestorage.com",
            access_key_id="access",
            secret_access_key="secret",
            public_bucket_name=public_bucket,
            private_bucket_name=private_bucket,
            public_base_url="https://assets.example.com/media",
            mode=mode,
        ),
    )


def test_default_storage_backend_is_local(monkeypatch):
    storage = importlib.import_module("clipforge_v3.services.storage_service")
    monkeypatch.delenv("V3_STORAGE_BACKEND", raising=False)
    assert storage.get_storage().backend == "local"


def test_r2_config_requires_values_without_leaking_secrets(monkeypatch):
    storage = importlib.import_module("clipforge_v3.services.storage_service")
    monkeypatch.setenv("V3_STORAGE_BACKEND", "r2")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "do-not-print")
    with pytest.raises(storage.StorageError) as exc:
        storage.get_storage()
    message = str(exc.value)
    assert "R2_PUBLIC_BUCKET_NAME" in message
    assert "R2_PRIVATE_BUCKET_NAME" in message
    assert "R2_BUCKET_NAME" in message
    assert "do-not-print" not in message


def test_r2_dual_bucket_config_initializes_from_account_id_and_prefers_dual(monkeypatch):
    storage = importlib.import_module("clipforge_v3.services.storage_service")
    monkeypatch.setenv("R2_ACCOUNT_ID", "abc123")
    monkeypatch.delenv("R2_ENDPOINT_URL", raising=False)
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "access")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_PUBLIC_BUCKET_NAME", "public-assets")
    monkeypatch.setenv("R2_PRIVATE_BUCKET_NAME", "private-videos")
    monkeypatch.setenv("R2_BUCKET_NAME", "legacy-bucket")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://assets.example.com/")
    config = storage._read_r2_config()
    assert config.endpoint_url == "https://abc123.r2.cloudflarestorage.com"
    assert config.region == "auto"
    assert config.public_base_url == "https://assets.example.com"
    assert config.mode == "dual"
    assert config.public_bucket_name == "public-assets"
    assert config.private_bucket_name == "private-videos"


def test_r2_single_bucket_legacy_config_remains_supported(monkeypatch):
    storage = importlib.import_module("clipforge_v3.services.storage_service")
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://acct.r2.cloudflarestorage.com")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "access")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.delenv("R2_PUBLIC_BUCKET_NAME", raising=False)
    monkeypatch.delenv("R2_PRIVATE_BUCKET_NAME", raising=False)
    monkeypatch.setenv("R2_BUCKET_NAME", "legacy-bucket")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://assets.example.com/")
    config = storage._read_r2_config()
    assert config.mode == "single"
    assert config.public_bucket_name == "legacy-bucket"
    assert config.private_bucket_name == "legacy-bucket"


@pytest.mark.parametrize(
    ("public_bucket", "private_bucket", "expected"),
    [
        ("public-assets", "", "R2_PRIVATE_BUCKET_NAME"),
        ("", "private-videos", "R2_PUBLIC_BUCKET_NAME"),
    ],
)
def test_r2_dual_bucket_partial_config_fails(monkeypatch, public_bucket, private_bucket, expected):
    storage = importlib.import_module("clipforge_v3.services.storage_service")
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://acct.r2.cloudflarestorage.com")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "access")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_PUBLIC_BUCKET_NAME", public_bucket)
    monkeypatch.setenv("R2_PRIVATE_BUCKET_NAME", private_bucket)
    monkeypatch.delenv("R2_BUCKET_NAME", raising=False)
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://assets.example.com/")
    with pytest.raises(storage.StorageError) as exc:
        storage._read_r2_config()
    assert expected in str(exc.value)


def test_r2_dual_bucket_rejects_identical_public_and_private_buckets(monkeypatch):
    storage = importlib.import_module("clipforge_v3.services.storage_service")
    monkeypatch.setenv("R2_ENDPOINT_URL", "https://acct.r2.cloudflarestorage.com")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "access")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_PUBLIC_BUCKET_NAME", "same-bucket")
    monkeypatch.setenv("R2_PRIVATE_BUCKET_NAME", "same-bucket")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://assets.example.com/")
    with pytest.raises(storage.StorageError) as exc:
        storage._read_r2_config()
    assert "must be different" in str(exc.value)


def test_object_key_blocks_path_traversal_and_encodes_public_url():
    storage = importlib.import_module("clipforge_v3.services.storage_service")
    with pytest.raises(storage.StorageError):
        storage.build_object_key("projects", 1, "../secret")
    key = storage.asset_object_key(project_id=1, digest="a" * 64, filename="产品 photo #1.png")
    assert key == "projects/1/assets/aaaaaaaaaaaaaaaaaaaaaaaa/photo_1.png"
    r2 = _r2_storage(FakeR2Client())
    assert r2.get_public_url(key) == "https://assets.example.com/media/projects/1/assets/aaaaaaaaaaaaaaaaaaaaaaaa/photo_1.png"


def test_r2_public_asset_upload_uses_public_bucket_and_reuses_existing_object(tmp_path):
    fake = FakeR2Client()
    r2 = _r2_storage(fake)
    image = tmp_path / "product.png"
    image.write_bytes(b"image")
    first = r2.save_public_asset(project_id=1, source_path=image, object_key="projects/1/assets/abc/product.png", content_type="image/png")
    second = r2.save_public_asset(project_id=1, source_path=image, object_key=first["object_key"], content_type="image/png")
    assert first["object_key"] == second["object_key"]
    assert first["size_bytes"] == 5
    assert first["access_url"] == "https://assets.example.com/media/projects/1/assets/abc/product.png"
    assert fake.put_calls == [
        {
            "Bucket": "clipforge-public-assets",
            "Key": "projects/1/assets/abc/product.png",
            "ContentType": "image/png",
            "CacheControl": "public, max-age=31536000",
        }
    ]


def test_r2_private_video_upload_uses_private_bucket_and_has_no_public_url(tmp_path):
    fake = FakeR2Client()
    r2 = _r2_storage(fake)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    stored = r2.save_private_video(project_id=1, source_path=video, object_key="projects/1/shots/1/submissions/1/video.mp4")
    assert stored["access_url"] is None
    assert fake.put_calls == [
        {
            "Bucket": "clipforge-private-videos",
            "Key": "projects/1/shots/1/submissions/1/video.mp4",
            "ContentType": "video/mp4",
            "CacheControl": "private, max-age=0",
        }
    ]


def test_r2_presigned_video_url_is_dynamic_private_and_clamped():
    fake = FakeR2Client()
    r2 = _r2_storage(fake)
    url = r2.get_download_url("projects/1/shots/1/submissions/1/video.mp4", expires_in=99_999)
    assert "X-Amz-Signature" in url
    assert fake.presign_calls[0]["expires"] == 3600
    assert fake.presign_calls[0]["params"]["Bucket"] == "clipforge-private-videos"
    assert fake.presign_calls[0]["params"]["Key"] == "projects/1/shots/1/submissions/1/video.mp4"


def test_r2_asset_upload_records_https_url_and_enters_provider_payload(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    storage = importlib.import_module("clipforge_v3.services.storage_service")
    fake = FakeR2Client()
    r2 = _r2_storage(fake)
    monkeypatch.setenv("V3_STORAGE_BACKEND", "r2")
    monkeypatch.setattr(storage, "R2Storage", lambda: r2)
    project_id = client.post("/v3/projects", data={k: str(v) for k, v in buffing_wheel_payload.items()}, follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    client.post(f"/v3/projects/{project_id}/product-truth/confirm")
    with sample_image_file.open("rb") as handle:
        response = client.post(
            f"/v3/projects/{project_id}/assets",
            data={"primary_role": "product_identity", "user_approved": "true", "is_identity_anchor": "true"},
            files={"asset_file": ("identity product.png", handle, "image/png")},
            follow_redirects=False,
        )
    assert response.status_code == 303
    asset = db_conn.execute("SELECT * FROM v3_assets WHERE project_id = ?", (int(project_id),)).fetchone()
    assert asset["storage_backend"] == "r2"
    assert asset["object_key"].startswith(f"projects/{project_id}/assets/")
    assert asset["content_type"] == "image/png"
    assert asset["size_bytes"] > 0
    assert asset["access_url"].startswith("https://assets.example.com/media/projects/")
    assert "secret" not in asset["access_url"]
    assert "X-Amz-Signature" not in asset["access_url"]
    assert fake.put_calls[0]["Bucket"] == "clipforge-public-assets"
    assert fake.put_calls[0]["ContentType"] == "image/png"
    assert fake.put_calls[0]["CacheControl"] == "public, max-age=31536000"
    assert all(call["Bucket"] != "clipforge-private-videos" for call in fake.put_calls)
    client.post(f"/v3/projects/{project_id}/director-plan/generate")
    client.post(f"/v3/projects/{project_id}/shots/confirm-all")
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    shot = dict(db_conn.execute("SELECT * FROM v3_shots WHERE project_id = ? ORDER BY sequence_index LIMIT 1", (int(project_id),)).fetchone())
    prompt = generation.compile_prompt(project_id=int(project_id), shot_id=shot["id"])
    image_urls = [item["image_url"]["url"] for item in prompt["provider_payload_json"]["content"] if item.get("type") == "image_url"]
    assert asset["access_url"] in image_urls


def test_r2_asset_upload_failure_does_not_create_available_asset(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    storage = importlib.import_module("clipforge_v3.services.storage_service")
    r2 = _r2_storage(FakeR2Client(fail_put=True))
    monkeypatch.setenv("V3_STORAGE_BACKEND", "r2")
    monkeypatch.setattr(storage, "R2Storage", lambda: r2)
    response = client.post("/v3/projects", data={k: str(v) for k, v in buffing_wheel_payload.items()}, follow_redirects=False)
    project_id = int(response.headers["location"].rsplit("/", 1)[-1])
    with sample_image_file.open("rb") as handle:
        upload = client.post(
            f"/v3/projects/{project_id}/assets",
            data={"primary_role": "product_identity"},
            files={"asset_file": ("identity.png", handle, "image/png")},
        )
    assert upload.status_code == 400
    assert _count_rows(db_conn, "v3_assets") == 0


def test_r2_provider_video_upload_success_is_idempotent_and_never_submits(client, db_conn, buffing_wheel_payload, sample_image_file, tmp_path, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    fake_provider = FakeArkProvider(video_url="https://cdn.example/video.mp4?sig=temporary")
    fake_provider.submit_task = Mock(side_effect=AssertionError("must not submit during recovery"))
    fake_r2 = FakeR2Client()
    project_id, shot, _prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    prompt = _compile_prompt_with_https_asset(generation, db_conn, project_id=project_id, shot=shot)
    submission = _reserve_existing_provider_submission(generation, project_id=project_id, shot=shot, prompt=prompt, status="downloading")
    video = tmp_path / "provider-video.mp4"
    first = tmp_path / "first.jpg"
    last = tmp_path / "last.jpg"
    video.write_bytes(b"video")
    first.write_bytes(b"first")
    last.write_bytes(b"last")
    downloads: list[str] = []

    def fake_download(video_url, project_id, shot, take_number):
        downloads.append(video_url)
        return str(video), [str(first), str(last)]

    monkeypatch.setattr(generation, "get_provider", lambda: fake_provider)
    monkeypatch.setattr(generation, "get_storage", lambda: _r2_storage(fake_r2))
    monkeypatch.setattr(generation, "_download_provider_video", fake_download)
    first_result = generation.recover_generation_submission(submission["id"])
    second_result = generation.recover_generation_submission(submission["id"])
    fake_provider.submit_task.assert_not_called()
    assert first_result["status"] == "succeeded"
    assert second_result["status"] == "succeeded"
    assert downloads == ["https://cdn.example/video.mp4?sig=temporary"]
    take = db_conn.execute("SELECT * FROM v3_takes WHERE generation_submission_id = ?", (submission["id"],)).fetchone()
    assert take["storage_backend"] == "r2"
    assert take["object_key"] == f"projects/{project_id}/shots/{shot['id']}/submissions/{submission['id']}/video.mp4"
    assert take["content_type"] == "video/mp4"
    assert take["size_bytes"] == 5
    assert take["local_path"] is None
    assert take["remote_url"] is None
    assert take["object_key"] not in (take["remote_url"] or "")
    assert _count_rows(db_conn, "v3_takes") == 1
    assert _usage_count_for_submission(db_conn, submission["id"]) == 1
    assert len(fake_r2.put_calls) == 1
    assert fake_r2.put_calls[0]["Bucket"] == "clipforge-private-videos"
    assert all(call["Bucket"] != "clipforge-public-assets" for call in fake_r2.put_calls)


def test_r2_provider_video_upload_failure_is_recoverable_and_never_submits(client, db_conn, buffing_wheel_payload, sample_image_file, tmp_path, monkeypatch):
    generation = importlib.import_module("clipforge_v3.services.generation_service")
    fake_provider = FakeArkProvider(video_url="https://cdn.example/video.mp4?sig=temporary")
    fake_provider.submit_task = Mock(side_effect=AssertionError("must not submit during recovery"))
    project_id, shot, _prompt = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    prompt = _compile_prompt_with_https_asset(generation, db_conn, project_id=project_id, shot=shot)
    submission = _reserve_existing_provider_submission(generation, project_id=project_id, shot=shot, prompt=prompt, status="download_failed")
    video = tmp_path / "provider-video.mp4"
    first = tmp_path / "first.jpg"
    last = tmp_path / "last.jpg"
    video.write_bytes(b"video")
    first.write_bytes(b"first")
    last.write_bytes(b"last")
    monkeypatch.setattr(generation, "get_provider", lambda: fake_provider)
    monkeypatch.setattr(generation, "get_storage", lambda: _r2_storage(FakeR2Client(fail_put=True)))
    monkeypatch.setattr(generation, "_download_provider_video", lambda *args, **kwargs: (str(video), [str(first), str(last)]))
    result = generation.recover_generation_submission(submission["id"])
    fake_provider.submit_task.assert_not_called()
    assert result["status"] == "storage_recovery_required"
    row = db_conn.execute("SELECT submission_status, provider_task_id, take_id, error_json FROM v3_generation_submissions WHERE id = ?", (submission["id"],)).fetchone()
    assert row["submission_status"] == "artifact_upload_failed"
    assert row["provider_task_id"] == "ark-task-existing"
    assert row["take_id"] is None
    assert "secret" not in row["error_json"]
    assert _count_rows(db_conn, "v3_takes") == 0
    assert _usage_count_for_submission(db_conn, submission["id"]) == 1


def test_object_storage_migration_is_repeatable_and_local_rows_remain_readable(app_env, db_conn):
    migrations = importlib.import_module("clipforge_v3.migrations")
    assert migrations.run_v3_migrations() == []
    assert migrations.run_v3_migrations() == []
    asset_columns = {row["name"] for row in db_conn.execute("PRAGMA table_info(v3_assets)").fetchall()}
    take_columns = {row["name"] for row in db_conn.execute("PRAGMA table_info(v3_takes)").fetchall()}
    assert {"storage_backend", "object_key", "content_type", "size_bytes"}.issubset(asset_columns)
    assert {"storage_backend", "object_key", "content_type", "size_bytes"}.issubset(take_columns)
    legacy_tables = {row["name"] for row in db_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "jobs" in legacy_tables


def test_r2_legacy_single_bucket_routes_public_and_private_to_same_bucket_without_schema_change(tmp_path):
    fake = FakeR2Client()
    r2 = _r2_storage(fake, public_bucket="legacy-bucket", private_bucket="legacy-bucket", mode="single")
    image = tmp_path / "product.png"
    video = tmp_path / "video.mp4"
    image.write_bytes(b"image")
    video.write_bytes(b"video")
    asset = r2.save_public_asset(project_id=1, source_path=image, object_key="projects/1/assets/abc/product.png", content_type="image/png")
    take = r2.save_private_video(project_id=1, source_path=video, object_key="projects/1/shots/1/submissions/1/video.mp4")
    assert asset["access_url"] == "https://assets.example.com/media/projects/1/assets/abc/product.png"
    assert take["access_url"] is None
    assert {call["Bucket"] for call in fake.put_calls} == {"legacy-bucket"}
    assert r2.get_download_url(take["object_key"]).startswith("https://r2-download.example/")
    assert fake.presign_calls[0]["params"]["Bucket"] == "legacy-bucket"
