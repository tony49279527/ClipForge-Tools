from __future__ import annotations

import os
import re
import shutil
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

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


class StorageAdapter(ABC):
    @abstractmethod
    def save_upload(self, *, project_id: int, upload: UploadFile) -> StoredObject:
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

    def save_upload(self, *, project_id: int, upload: UploadFile) -> StoredObject:
        mime_type = upload.content_type or "application/octet-stream"
        if mime_type not in ALLOWED_MIME:
            raise StorageError(f"Unsupported file type: {mime_type}. Use JPG, PNG, WEBP, MP4, WAV, MP3, PDF, or TXT.")
        project_dir = _safe_project_dir(project_id)
        safe_name = sanitize_filename(upload.filename or "asset")
        destination = (project_dir / f"{uuid.uuid4().hex}_{safe_name}").resolve()
        if not str(destination).startswith(str(project_dir)):
            raise StorageError("Unsafe upload destination.")
        bytes_written = 0
        with destination.open("wb") as buffer:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_BYTES:
                    destination.unlink(missing_ok=True)
                    raise StorageError(f"File is too large. Maximum allowed size is {MAX_UPLOAD_BYTES} bytes.")
                buffer.write(chunk)
        if bytes_written == 0:
            destination.unlink(missing_ok=True)
            raise StorageError("Uploaded file is empty.")
        return StoredObject(
            backend=self.backend,
            local_path=str(destination),
            access_url=f"/v3/storage/local/{project_id}/{destination.name}",
            mime_type=mime_type,
            size_bytes=bytes_written,
            original_filename=safe_name,
        )

    def temporary_url(self, path: str) -> str:
        resolved = Path(path).resolve()
        if not str(resolved).startswith(str((UPLOADS_DIR / "v3").resolve())):
            raise StorageError("Refusing to create URL for unmanaged file.")
        return str(resolved)

    def delete_project(self, project_id: int) -> None:
        project_dir = _safe_project_dir(project_id)
        if project_dir.exists():
            shutil.rmtree(project_dir)


class CloudStorage(StorageAdapter):
    def save_upload(self, *, project_id: int, upload: UploadFile) -> StoredObject:
        raise StorageError("Cloud storage is not configured. Set STORAGE_BACKEND=local or implement the cloud adapter.")

    def temporary_url(self, path: str) -> str:
        raise StorageError("Cloud storage is not configured.")

    def delete_project(self, project_id: int) -> None:
        raise StorageError("Cloud storage is not configured.")


def get_storage() -> StorageAdapter:
    backend = os.getenv("STORAGE_BACKEND", "local").lower()
    if backend == "local":
        return LocalStorage()
    if backend in {"gcs", "s3", "r2"}:
        return CloudStorage()
    raise StorageError(f"Unsupported storage backend: {backend}")

