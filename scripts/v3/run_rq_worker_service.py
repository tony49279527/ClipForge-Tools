from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from clipforge_v3.services.queue_config import resolve_redis_settings


WORKER_PROCESS: subprocess.Popen | None = None
STOPPING = threading.Event()


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path not in {"/", "/health", "/ready"}:
            self.send_response(404)
            self.end_headers()
            return
        alive = WORKER_PROCESS is not None and WORKER_PROCESS.poll() is None
        status = 200 if alive else 503
        body = {
            "ok": alive,
            "worker_alive": alive,
            "queue_name": os.getenv("RQ_QUEUE_NAME", "clipforge"),
            "stopping": STOPPING.is_set(),
        }
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        return


def _start_health_server() -> ThreadingHTTPServer:
    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, name="worker-health-server", daemon=True)
    thread.start()
    return server


def _start_worker() -> subprocess.Popen:
    settings = resolve_redis_settings()
    worker_count = os.getenv("RQ_WORKER_COUNT", "1")
    env = os.environ.copy()
    env["REDIS_URL"] = settings.redis_url
    env["RQ_QUEUE_NAME"] = settings.queue_name
    return subprocess.Popen(
        [sys.executable, "worker.py", "--workers", worker_count],
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


def _terminate_worker(signum=None, frame=None) -> None:
    STOPPING.set()
    process = WORKER_PROCESS
    if process is not None and process.poll() is None:
        process.terminate()


def main() -> int:
    global WORKER_PROCESS
    signal.signal(signal.SIGTERM, _terminate_worker)
    signal.signal(signal.SIGINT, _terminate_worker)
    server = _start_health_server()
    try:
        WORKER_PROCESS = _start_worker()
        while True:
            code = WORKER_PROCESS.poll()
            if code is not None:
                return int(code or 0)
            if STOPPING.is_set():
                break
            time.sleep(1)
        WORKER_PROCESS.wait(timeout=int(os.getenv("WORKER_SHUTDOWN_GRACE_SECONDS", "60")))
        return int(WORKER_PROCESS.returncode or 0)
    except subprocess.TimeoutExpired:
        if WORKER_PROCESS is not None:
            WORKER_PROCESS.kill()
        return 124
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
