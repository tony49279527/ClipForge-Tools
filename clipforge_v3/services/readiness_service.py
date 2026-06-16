from __future__ import annotations

import os
import shutil
from pathlib import Path

from db import get_conn
from task_queue import get_redis

from clipforge_v3.providers.config import SEEDANCE_BASE_URL, SEEDANCE_MODEL
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
        get_redis().ping()
        return {"ok": True, "message": "Redis connection ok."}
    except Exception as exc:
        return {"ok": False, "message": f"Redis unavailable. Next step: start redis-server or set REDIS_URL. {exc}"}


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
        backend = os.getenv("STORAGE_BACKEND", "local")
        get_storage()
        root = Path(os.getenv("UPLOADS_DIR", "uploads")).resolve()
        root.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "message": f"Storage backend active: {backend}.", "backend": backend}
    except Exception as exc:
        return {"ok": False, "message": f"Storage check failed. Next step: set STORAGE_BACKEND=local. {exc}"}


def _check_worker() -> dict:
    try:
        redis = get_redis()
        workers = redis.keys("rq:worker:*")
        return {"ok": True, "message": f"Worker records visible: {len(workers)}.", "worker_count": len(workers)}
    except Exception as exc:
        return {"ok": False, "message": f"Worker check failed. Next step: run python worker.py. {exc}"}


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

