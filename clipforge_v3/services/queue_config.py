from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


class RedisConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class RedisSettings:
    redis_url: str
    source: str
    queue_name: str
    required: bool
    uses_localhost: bool


def truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def is_cloud_runtime() -> bool:
    return bool(os.getenv("K_SERVICE") or os.getenv("K_REVISION") or os.getenv("K_CONFIGURATION"))


def redis_required() -> bool:
    if truthy(os.getenv("REDIS_REQUIRED")):
        return True
    if truthy(os.getenv("ALLOW_LOCAL_REDIS")):
        return False
    return is_cloud_runtime()


def _is_localhost_url(url: str) -> bool:
    try:
        host = urlsplit(url).hostname or ""
    except Exception:
        return False
    return host in {"localhost", "127.0.0.1", "::1"}


def resolve_redis_settings() -> RedisSettings:
    url = os.getenv("REDIS_URL", "").strip()
    source = "REDIS_URL"
    if not url:
        url = os.getenv("RQ_REDIS_URL", "").strip()
        source = "RQ_REDIS_URL"
    required = redis_required()
    if not url:
        if required:
            raise RedisConfigurationError("Redis is required but REDIS_URL is not configured.")
        url = "redis://localhost:6379/0"
        source = "default_localhost"
    uses_localhost = _is_localhost_url(url)
    if required and uses_localhost and not truthy(os.getenv("ALLOW_LOCAL_REDIS")):
        raise RedisConfigurationError("Redis is required; refusing to use localhost Redis in this runtime.")
    queue_name = os.getenv("RQ_QUEUE_NAME", "clipforge").strip() or "clipforge"
    return RedisSettings(redis_url=url, source=source, queue_name=queue_name, required=required, uses_localhost=uses_localhost)


def redact_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except Exception:
        return "<invalid-url>"
    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    if parsed.username or parsed.password:
        netloc = f"<redacted>@{netloc}"
    path = parsed.path or ""
    return urlunsplit((parsed.scheme, netloc, path, "", ""))
