from __future__ import annotations

import importlib
import json
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from types import SimpleNamespace

import pytest


def _reload_queue_config():
    return importlib.reload(importlib.import_module("clipforge_v3.services.queue_config"))


def test_local_environment_allows_default_localhost(monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("REDIS_REQUIRED", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("RQ_REDIS_URL", raising=False)
    config = _reload_queue_config()
    settings = config.resolve_redis_settings()
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.required is False
    assert settings.uses_localhost is True


def test_cloud_runtime_without_redis_fails_explicitly(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "clipforge-tools")
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("RQ_REDIS_URL", raising=False)
    config = _reload_queue_config()
    with pytest.raises(config.RedisConfigurationError) as exc:
        config.resolve_redis_settings()
    assert "REDIS_URL" in str(exc.value)


def test_redis_url_takes_priority_over_rq_redis_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://primary.example:6379/0")
    monkeypatch.setenv("RQ_REDIS_URL", "redis://legacy.example:6379/0")
    settings = _reload_queue_config().resolve_redis_settings()
    assert settings.redis_url == "redis://primary.example:6379/0"
    assert settings.source == "REDIS_URL"


def test_rq_redis_url_is_compatible_fallback(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("RQ_REDIS_URL", "redis://legacy.example:6379/1")
    settings = _reload_queue_config().resolve_redis_settings()
    assert settings.redis_url == "redis://legacy.example:6379/1"
    assert settings.source == "RQ_REDIS_URL"


def test_redact_url_hides_password():
    config = _reload_queue_config()
    redacted = config.redact_url("redis://user:secret-password@redis.internal:6379/0")
    assert "secret-password" not in redacted
    assert redacted == "redis://<redacted>@redis.internal:6379/0"


class FakeRedis:
    def __init__(self, *, workers=None, ttls=None):
        self.workers = workers or {}
        self.ttls = ttls or {}

    def ping(self):
        return True

    def keys(self, pattern):
        assert pattern == "rq:worker:*"
        return list(self.workers)

    def hgetall(self, key):
        return self.workers.get(key, {})

    def ttl(self, key):
        return self.ttls.get(key, -1)


def test_readiness_reports_worker_healthy(monkeypatch):
    readiness = importlib.reload(importlib.import_module("clipforge_v3.services.readiness_service"))
    now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setenv("WORKER_REQUIRED", "true")
    monkeypatch.setenv("WORKER_HEARTBEAT_MAX_AGE_SECONDS", "120")
    monkeypatch.setenv("REDIS_URL", "redis://redis.internal:6379/0")
    monkeypatch.setattr(readiness, "get_redis", lambda: FakeRedis(workers={"rq:worker:clipforge-w1": {"last_heartbeat": now}}, ttls={"rq:worker:clipforge-w1": 300}))
    result = readiness._check_worker()
    assert result["ok"] is True
    assert result["registered_workers"] == 1
    assert result["healthy_workers"] == 1


def test_readiness_fails_when_worker_heartbeat_is_stale(monkeypatch):
    readiness = importlib.reload(importlib.import_module("clipforge_v3.services.readiness_service"))
    stale = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    monkeypatch.setenv("WORKER_REQUIRED", "true")
    monkeypatch.setenv("WORKER_HEARTBEAT_MAX_AGE_SECONDS", "120")
    monkeypatch.setenv("REDIS_URL", "redis://redis.internal:6379/0")
    monkeypatch.setattr(readiness, "get_redis", lambda: FakeRedis(workers={"rq:worker:clipforge-w1": {"last_heartbeat": stale}}))
    result = readiness._check_worker()
    assert result["ok"] is False
    assert result["registered_workers"] == 1
    assert result["healthy_workers"] == 0


def test_readiness_accepts_live_worker_ttl_even_if_heartbeat_field_is_stale(monkeypatch):
    readiness = importlib.reload(importlib.import_module("clipforge_v3.services.readiness_service"))
    stale = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    monkeypatch.setenv("WORKER_REQUIRED", "true")
    monkeypatch.setenv("WORKER_HEARTBEAT_MAX_AGE_SECONDS", "120")
    monkeypatch.setenv("REDIS_URL", "redis://redis.internal:6379/0")
    monkeypatch.setattr(
        readiness,
        "get_redis",
        lambda: FakeRedis(
            workers={"rq:worker:clipforge-w1": {"last_heartbeat": stale}},
            ttls={"rq:worker:clipforge-w1": 300},
        ),
    )
    result = readiness._check_worker()
    assert result["ok"] is True
    assert result["healthy_workers"] == 1


def test_readiness_redis_failure_does_not_leak_password(monkeypatch):
    readiness = importlib.reload(importlib.import_module("clipforge_v3.services.readiness_service"))
    monkeypatch.setenv("REDIS_URL", "redis://user:super-secret@redis.internal:6379/0")

    class BrokenRedis:
        def ping(self):
            raise RuntimeError("connection refused for user")

    monkeypatch.setattr(readiness, "get_redis", lambda: BrokenRedis())
    result = readiness._check_redis()
    assert result["ok"] is False
    assert "super-secret" not in json.dumps(result)
    assert result["queue_name"] == "clipforge"


def test_worker_service_health_fails_when_worker_process_exits(monkeypatch):
    worker_service = importlib.reload(importlib.import_module("scripts.v3.run_rq_worker_service"))
    process = SimpleNamespace(poll=lambda: 1)
    monkeypatch.setenv("PORT", "0")
    monkeypatch.setattr(worker_service, "WORKER_PROCESS", process)
    server = worker_service._start_health_server()
    try:
        host, port = server.server_address
        conn = HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/health")
        response = conn.getresponse()
        body = json.loads(response.read().decode("utf-8"))
        assert response.status == 503
        assert body["worker_alive"] is False
    finally:
        server.shutdown()
        server.server_close()


def test_worker_name_includes_runtime_identifier(monkeypatch):
    worker = importlib.reload(importlib.import_module("worker"))
    monkeypatch.setenv("K_REVISION", "clipforge-tools-worker-00003-v6r")
    assert worker.worker_name(1) == "clipforge-clipforge-tools-worker-00003-v6r-w1"


def test_queue_smoke_wrapper_has_no_project_side_effect():
    task_queue = importlib.reload(importlib.import_module("task_queue"))
    assert task_queue.run_queue_smoke_wrapper({"echo": "hello"}) == {"ok": True, "echo": "hello", "queue": "clipforge"}
