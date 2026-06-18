from __future__ import annotations

import os
import shutil
from pathlib import Path

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
        workers = redis.keys("rq:worker:*")
        healthy = []
        heartbeat_ages: list[float] = []
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        for key in workers:
            data = redis.hgetall(key) or {}
            raw = data.get("last_heartbeat") or data.get(b"last_heartbeat") or data.get("birth")
            age = None
            if raw:
                try:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    heartbeat = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                    if heartbeat.tzinfo is None:
                        heartbeat = heartbeat.replace(tzinfo=timezone.utc)
                    age = max((now - heartbeat).total_seconds(), 0)
                    heartbeat_ages.append(age)
                except Exception:
                    age = None
            if age is None or age <= max_age:
                healthy.append(key)
        ok = bool(healthy) if required else True
        return {
            "ok": ok,
            "required": required,
            "registered_workers": len(workers),
            "healthy_workers": len(healthy),
            "heartbeat_age": min(heartbeat_ages) if heartbeat_ages else None,
            "message": f"Worker records visible: {len(workers)}; healthy: {len(healthy)}.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "required": required,
            "registered_workers": 0,
            "healthy_workers": 0,
            "heartbeat_age": None,
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
    ok = all(checks[name]["ok"] for name in required)
    return {"ok": ok, "checks": checks}
