from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from rq import Queue, Worker

from db import get_conn
from task_queue import get_redis

from clipforge_v3.providers.config import SEEDANCE_BASE_URL, SEEDANCE_MODEL
from clipforge_v3.services.queue_config import RedisConfigurationError, resolve_redis_settings
from clipforge_v3.services.storage_service import get_storage


def _check_database() -> dict:
    try:
        conn = get_conn()
        conn.execute("SELECT 1")
        conn.close()
        return {"ok": True, "message": "Database connection ok."}
    except Exception as exc:
        return {"ok": False, "message": f"Database check failed. Next step: verify DB_URL or DB_PATH. {exc}"}


def _check_redis() -> dict:
    try:
        settings = resolve_redis_settings()
    except RedisConfigurationError as exc:
        return {
            "ok": False,
            "configured": False,
            "reachable": False,
            "queue_name": os.getenv("RQ_QUEUE_NAME", "clipforge"),
            "message": f"Redis configuration failed. Next step: configure REDIS_URL through Secret Manager. {exc}",
        }
    try:
        get_redis().ping()
        return {
            "ok": True,
            "configured": True,
            "reachable": True,
            "queue_name": settings.queue_name,
            "message": "Redis connection ok.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "configured": True,
            "reachable": False,
            "queue_name": settings.queue_name,
            "message": f"Redis unavailable. Next step: verify REDIS_URL and network access. {exc}",
        }


def _check_ffmpeg() -> dict:
    if shutil.which("ffmpeg") and shutil.which("ffprobe"):
        return {"ok": True, "message": "FFmpeg and ffprobe available."}
    return {"ok": False, "message": "FFmpeg or ffprobe missing. Next step: install ffmpeg before generation."}


def _check_provider() -> dict:
    configured = bool(SEEDANCE_MODEL and SEEDANCE_BASE_URL)
    return {
        "ok": configured,
        "message": "Provider configured." if configured else "Provider config incomplete. Next step: set SEEDANCE_MODEL and SEEDANCE_BASE_URL.",
        "provider": os.getenv("SEEDANCE_PROVIDER", "ark"),
        "model_configured": bool(SEEDANCE_MODEL),
        "base_url_configured": bool(SEEDANCE_BASE_URL),
        "api_key_configured": bool(os.getenv("ARK_API_KEY")),
    }


def _check_storage() -> dict:
    try:
        storage = get_storage()
        backend = getattr(storage, "backend", "unknown")
        root = Path(os.getenv("UPLOADS_DIR", "uploads")).resolve()
        root.mkdir(parents=True, exist_ok=True)
        configured_backend = os.getenv("V3_STORAGE_BACKEND", os.getenv("STORAGE_BACKEND", "local")).strip().lower()
        return {
            "ok": True,
            "message": f"Storage backend active: {backend}.",
            "backend": backend,
            "configured_backend": configured_backend,
        }
    except Exception as exc:
        return {"ok": False, "message": f"Storage check failed. Next step: verify V3_STORAGE_BACKEND or set V3_STORAGE_BACKEND=local. {exc}"}


def _check_worker() -> dict:
    max_age = int(os.getenv("WORKER_HEARTBEAT_MAX_AGE_SECONDS", "120"))
    required = os.getenv("WORKER_REQUIRED", "false").strip().lower() in {"1", "true", "yes", "on"} or os.getenv("REDIS_REQUIRED", "").strip().lower() in {"1", "true", "yes", "on"}
    try:
        redis = get_redis()
        settings = resolve_redis_settings()
        queue = Queue(settings.queue_name, connection=redis)
        workers = Worker.all(queue=queue)
        healthy = []
        heartbeat_ages: list[float] = []

        now = datetime.now(timezone.utc)
        for worker in workers:
            age = None
            ttl = None
            try:
                ttl = redis.ttl(worker.key)
            except Exception:
                ttl = None

            heartbeat = getattr(worker, "last_heartbeat", None) or getattr(worker, "birth_date", None)
            if heartbeat:
                try:
                    if heartbeat.tzinfo is None:
                        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
                    age = max((now - heartbeat).total_seconds(), 0)
                    heartbeat_ages.append(age)
                except Exception:
                    age = None
            if (isinstance(ttl, int) and ttl > 0) or age is None or age <= max_age:
                healthy.append(worker)
        ok = bool(healthy) if required else True
        return {
            "ok": ok,
            "required": required,
            "registered_workers": len(workers),
            "healthy_workers": len(healthy),
            "heartbeat_age": min(heartbeat_ages) if heartbeat_ages else None,
            "queue_name": settings.queue_name,
            "message": f"RQ workers visible on queue {settings.queue_name}: {len(workers)}; healthy: {len(healthy)}.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "required": required,
            "registered_workers": 0,
            "healthy_workers": 0,
            "heartbeat_age": None,
            "queue_name": os.getenv("RQ_QUEUE_NAME", "clipforge"),
            "message": f"Worker check failed. Next step: run the RQ worker service. {exc}",
        }


def readiness() -> dict:
    checks = {
        "database": _check_database(),
        "redis": _check_redis(),
        "ffmpeg": _check_ffmpeg(),
        "provider": _check_provider(),
        "storage": _check_storage(),
        "worker": _check_worker(),
    }
    required = {"database", "ffmpeg", "provider", "storage"}
    if checks["worker"].get("required"):
        required.add("worker")
    ok = all(checks[name]["ok"] for name in required)
    return {"ok": ok, "checks": checks}
