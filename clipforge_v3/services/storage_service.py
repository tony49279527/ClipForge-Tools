from __future__ import annotations

import os
import re
import shutil
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from fastapi import UploadFile


BASE_DIR = Path(__file__).resolve().parents[2]
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", str(BASE_DIR / "uploads"))).resolve()
MAX_UPLOAD_BYTES = int(os.getenv("V3_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
MAX_IMAGE_PIXELS = int(os.getenv("V3_MAX_IMAGE_PIXELS", str(24_000_000)))
ALLOWED_IMAGE_MIME = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_VIDEO_MIME = {"video/mp4", "video/quicktime", "video/webm"}
ALLOWED_AUDIO_MIME = {"audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4"}
ALLOWED_DOCUMENT_MIME = {"application/pdf", "text/plain"}
ALLOWED_MIME = ALLOWED_IMAGE_MIME | ALLOWED_VIDEO_MIME | ALLOWED_AUDIO_MIME | ALLOWED_DOCUMENT_MIME


class StorageError(ValueError):
    pass


class StoredObject(dict):
    pass


@dataclass(frozen=True)
class R2Config:
    endpoint_url: str
    access_key_id: str
    secret_access_key: str
    bucket_name: str
    public_base_url: str
    region: str = "auto"


class StorageAdapter(ABC):
    backend = "unknown"

    @abstractmethod
    def save_upload(self, *, project_id: int, upload: UploadFile) -> StoredObject:
        raise NotImplementedError

    @abstractmethod
    def save_file(self, *, project_id: int, source_path: Path, object_key: str | None = None, content_type: str | None = None) -> StoredObject:
        raise NotImplementedError

    @abstractmethod
    def open(self, path_or_key: str):
        raise NotImplementedError

    @abstractmethod
    def exists(self, path_or_key: str, *, expected_size: int | None = None) -> bool:
        raise NotImplementedError

    @abstractmethod
    def delete(self, path_or_key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_object_key(self, stored: StoredObject | dict) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def get_public_url(self, object_key: str) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def get_download_url(self, object_key: str, *, expires_in: int = 1800) -> str:
        raise NotImplementedError

    @abstractmethod
    def temporary_url(self, path: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def delete_project(self, project_id: int) -> None:
        raise NotImplementedError


def sanitize_filename(filename: str) -> str:
    name = Path(filename or "asset").name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return name or "asset"


def sanitize_object_key_part(value: str) -> str:
    part = sanitize_filename(value)
    if part in {"", ".", ".."}:
        raise StorageError("Invalid object key component.")
    return part


def build_object_key(*parts: str | int) -> str:
    safe_parts: list[str] = []
    for value in parts:
        text = str(value).strip()
        if "/" in text or "\\" in text or text in {"", ".", ".."}:
            raise StorageError("Invalid object key component.")
        safe_parts.append(sanitize_object_key_part(text))
    key = "/".join(safe_parts)
    if key.startswith("/") or ".." in key.split("/"):
        raise StorageError("Unsafe object key.")
    return key


def asset_object_key(*, project_id: int, digest: str, filename: str) -> str:
    return build_object_key("projects", project_id, "assets", digest[:24], sanitize_filename(filename))


def take_video_object_key(*, project_id: int, shot_id: int, submission_id: int) -> str:
    return build_object_key("projects", project_id, "shots", shot_id, "submissions", submission_id, "video.mp4")


def _write_upload_to_local(*, project_id: int, upload: UploadFile) -> StoredObject:
    import hashlib

    mime_type = upload.content_type or "application/octet-stream"
    if mime_type not in ALLOWED_MIME:
        raise StorageError(f"Unsupported file type: {mime_type}. Use JPG, PNG, WEBP, MP4, WAV, MP3, PDF, or TXT.")
    project_dir = _safe_project_dir(project_id)
    safe_name = sanitize_filename(upload.filename or "asset")
    destination = (project_dir / f"{uuid.uuid4().hex}_{safe_name}").resolve()
    if not str(destination).startswith(str(project_dir)):
        raise StorageError("Unsafe upload destination.")
    digest = hashlib.sha256()
    bytes_written = 0
    try:
        upload.file.seek(0)
    except Exception:
        pass
    with destination.open("wb") as buffer:
        while True:
            chunk = upload.file.read(1024 * 1024)
            if not chunk:
                break
            bytes_written += len(chunk)
            if bytes_written > MAX_UPLOAD_BYTES:
                destination.unlink(missing_ok=True)
                raise StorageError(f"File is too large. Maximum allowed size is {MAX_UPLOAD_BYTES} bytes.")
            digest.update(chunk)
            buffer.write(chunk)
    if bytes_written == 0:
        destination.unlink(missing_ok=True)
        raise StorageError("Uploaded file is empty.")
    return StoredObject(
        backend="local",
        local_path=str(destination),
        access_url=f"/v3/storage/local/{project_id}/{destination.name}",
        mime_type=mime_type,
        content_type=mime_type,
        size_bytes=bytes_written,
        original_filename=safe_name,
        digest=digest.hexdigest(),
    )


def _safe_project_dir(project_id: int) -> Path:
    if project_id < 1:
        raise StorageError("Invalid project id for storage path.")
    root = (UPLOADS_DIR / "v3").resolve()
    project_dir = (root / str(project_id)).resolve()
    if not str(project_dir).startswith(str(root)):
        raise StorageError("Storage path validation failed.")
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir


class LocalStorage(StorageAdapter):
    backend = "local"
    base_dir = str((UPLOADS_DIR / "v3").resolve())

    def save_upload(self, *, project_id: int, upload: UploadFile) -> StoredObject:
        stored = _write_upload_to_local(project_id=project_id, upload=upload)
        stored["backend"] = self.backend
        return stored

    def save_file(self, *, project_id: int, source_path: Path, object_key: str | None = None, content_type: str | None = None) -> StoredObject:
        source = source_path.resolve()
        if not source.exists() or not source.is_file():
            raise StorageError("Source file does not exist.")
        project_dir = _safe_project_dir(project_id)
        safe_name = sanitize_filename(source.name)
        destination = (project_dir / f"{uuid.uuid4().hex}_{safe_name}").resolve()
        if not str(destination).startswith(str(project_dir)):
            raise StorageError("Unsafe storage destination.")
        if source != destination:
            shutil.copy2(source, destination)
        size_bytes = destination.stat().st_size
        return StoredObject(
            backend=self.backend,
            local_path=str(destination),
            access_url=f"/v3/storage/local/{project_id}/{destination.name}",
            object_key=None,
            mime_type=content_type or "application/octet-stream",
            content_type=content_type or "application/octet-stream",
            size_bytes=size_bytes,
            original_filename=safe_name,
        )

    def open(self, path_or_key: str):
        return Path(path_or_key).open("rb")

    def exists(self, path_or_key: str, *, expected_size: int | None = None) -> bool:
        path = Path(path_or_key)
        if not path.exists() or not path.is_file():
            return False
        return expected_size is None or path.stat().st_size == expected_size

    def delete(self, path_or_key: str) -> None:
        Path(path_or_key).unlink(missing_ok=True)

    def get_object_key(self, stored: StoredObject | dict) -> str | None:
        return stored.get("object_key")

    def get_public_url(self, object_key: str) -> str | None:
        return None

    def get_download_url(self, object_key: str, *, expires_in: int = 1800) -> str:
        raise StorageError("Local storage does not create presigned object URLs.")

    def temporary_url(self, path: str) -> str:
        resolved = Path(path).resolve()
        if not str(resolved).startswith(str((UPLOADS_DIR / "v3").resolve())):
            raise StorageError("Refusing to create URL for unmanaged file.")
        return str(resolved)

    def delete_project(self, project_id: int) -> None:
        project_dir = _safe_project_dir(project_id)
        if project_dir.exists():
            shutil.rmtree(project_dir)


def _read_r2_config() -> R2Config:
    account_id = os.getenv("R2_ACCOUNT_ID", "").strip()
    endpoint_url = os.getenv("R2_ENDPOINT_URL", "").strip()
    if not endpoint_url and account_id:
        endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"
    access_key_id = os.getenv("R2_ACCESS_KEY_ID", "").strip()
    secret_access_key = os.getenv("R2_SECRET_ACCESS_KEY", "").strip()
    bucket_name = os.getenv("R2_BUCKET_NAME", "").strip()
    public_base_url = os.getenv("R2_PUBLIC_BASE_URL", "").strip().rstrip("/")
    missing = [
        name
        for name, value in {
            "R2_ENDPOINT_URL or R2_ACCOUNT_ID": endpoint_url,
            "R2_ACCESS_KEY_ID": access_key_id,
            "R2_SECRET_ACCESS_KEY": secret_access_key,
            "R2_BUCKET_NAME": bucket_name,
            "R2_PUBLIC_BASE_URL": public_base_url,
        }.items()
        if not value
    ]
    if missing:
        raise StorageError(f"R2 storage is missing required configuration: {', '.join(missing)}.")
    if not public_base_url.startswith("https://"):
        raise StorageError("R2_PUBLIC_BASE_URL must be an HTTPS URL.")
    return R2Config(
        endpoint_url=endpoint_url,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        bucket_name=bucket_name,
        public_base_url=public_base_url,
    )


class R2Storage(StorageAdapter):
    backend = "r2"

    def __init__(self, *, client=None, config: R2Config | None = None):
        self.config = config or _read_r2_config()
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "s3",
                endpoint_url=self.config.endpoint_url,
                aws_access_key_id=self.config.access_key_id,
                aws_secret_access_key=self.config.secret_access_key,
                region_name=self.config.region,
                config=Config(
                    signature_version="s3v4",
                    connect_timeout=5,
                    read_timeout=60,
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
            )
        return self._client

    def _head(self, object_key: str):
        return self.client.head_object(Bucket=self.config.bucket_name, Key=object_key)

    def save_upload(self, *, project_id: int, upload: UploadFile) -> StoredObject:
        local = _write_upload_to_local(project_id=project_id, upload=upload)
        object_key = asset_object_key(project_id=project_id, digest=local["digest"], filename=local["original_filename"])
        stored = self.save_file(project_id=project_id, source_path=Path(local["local_path"]), object_key=object_key, content_type=local["content_type"])
        stored["local_path"] = local["local_path"]
        stored["original_filename"] = local["original_filename"]
        stored["digest"] = local["digest"]
        return stored

    def save_file(self, *, project_id: int, source_path: Path, object_key: str | None = None, content_type: str | None = None) -> StoredObject:
        source = source_path.resolve()
        if not source.exists() or not source.is_file():
            raise StorageError("Source file does not exist.")
        key = object_key or build_object_key("projects", project_id, "files", uuid.uuid4().hex, sanitize_filename(source.name))
        size_bytes = source.stat().st_size
        if self.exists(key, expected_size=size_bytes):
            return StoredObject(
                backend=self.backend,
                local_path=str(source),
                object_key=key,
                access_url=self.get_public_url(key),
                mime_type=content_type or "application/octet-stream",
                content_type=content_type or "application/octet-stream",
                size_bytes=size_bytes,
                original_filename=sanitize_filename(source.name),
            )
        try:
            with source.open("rb") as handle:
                self.client.put_object(
                    Bucket=self.config.bucket_name,
                    Key=key,
                    Body=handle,
                    ContentType=content_type or "application/octet-stream",
                    CacheControl="public, max-age=31536000" if (content_type or "").startswith("image/") else "private, max-age=0",
                )
        except Exception as exc:
            raise StorageError("R2 upload failed.") from exc
        if not self.exists(key, expected_size=size_bytes):
            raise StorageError("R2 upload verification failed.")
        return StoredObject(
            backend=self.backend,
            local_path=str(source),
            object_key=key,
            access_url=self.get_public_url(key),
            mime_type=content_type or "application/octet-stream",
            content_type=content_type or "application/octet-stream",
            size_bytes=size_bytes,
            original_filename=sanitize_filename(source.name),
        )

    def open(self, path_or_key: str):
        return self.client.get_object(Bucket=self.config.bucket_name, Key=path_or_key)["Body"]

    def exists(self, path_or_key: str, *, expected_size: int | None = None) -> bool:
        try:
            head = self._head(path_or_key)
        except Exception:
            return False
        size = int(head.get("ContentLength") or 0)
        return expected_size is None or size == expected_size

    def delete(self, path_or_key: str) -> None:
        self.client.delete_object(Bucket=self.config.bucket_name, Key=path_or_key)

    def get_object_key(self, stored: StoredObject | dict) -> str | None:
        return stored.get("object_key")

    def get_public_url(self, object_key: str) -> str | None:
        key = object_key.strip("/")
        return f"{self.config.public_base_url}/{quote(key, safe='/')}"

    def get_download_url(self, object_key: str, *, expires_in: int = 1800) -> str:
        expires = min(max(int(expires_in), 300), 3600)
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.config.bucket_name, "Key": object_key},
            ExpiresIn=expires,
        )

    def temporary_url(self, path: str) -> str:
        return self.get_download_url(path)

    def delete_project(self, project_id: int) -> None:
        raise StorageError("R2 project deletion is intentionally not implemented for alpha.")


def get_storage() -> StorageAdapter:
    backend = os.getenv("V3_STORAGE_BACKEND", os.getenv("STORAGE_BACKEND", "local")).strip().lower()
    if backend == "local":
        return LocalStorage()
    if backend == "r2":
        return R2Storage()
    raise StorageError(f"Unsupported storage backend: {backend}")
