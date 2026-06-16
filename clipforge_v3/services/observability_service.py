from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

from clipforge_v3.repositories import project_repository


logger = logging.getLogger("clipforge.v3")
SECRET_PATTERNS = [
    re.compile(r"(api[_-]?key|authorization|token|secret|credential)", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]+", re.IGNORECASE),
]


def new_request_id() -> str:
    return uuid.uuid4().hex


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, item in value.items():
            if any(pattern.search(str(key)) for pattern in SECRET_PATTERNS):
                sanitized[key] = "***"
            else:
                sanitized[key] = sanitize(item)
        return sanitized
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        output = value
        for pattern in SECRET_PATTERNS:
            output = pattern.sub("***", output)
        return output
    return value


def log_event(
    *,
    stage: str,
    status: str,
    request_id: str | None = None,
    project_id: int | None = None,
    shot_id: int | None = None,
    take_id: int | None = None,
    task_id: str | None = None,
    provider: str | None = None,
    duration: float = 0,
    error_code: str | None = None,
    retry_count: int = 0,
    elapsed_time: float = 0,
    message: str = "",
) -> int:
    event = {
        "request_id": request_id or new_request_id(),
        "project_id": project_id,
        "shot_id": shot_id,
        "take_id": take_id,
        "task_id": task_id,
        "provider": provider,
        "stage": stage,
        "duration": duration,
        "status": status,
        "error_code": error_code,
        "retry_count": retry_count,
        "elapsed_time": elapsed_time,
        "message": message,
    }
    clean = sanitize(event)
    logger.info("clipforge_v3_event %s", json.dumps(clean, ensure_ascii=False, sort_keys=True))
    return project_repository.create_operation_event(clean)


def list_recent_events(project_id: int | None = None, limit: int = 20) -> list[dict]:
    return [dict(row) for row in project_repository.list_operation_events(project_id, limit)]

