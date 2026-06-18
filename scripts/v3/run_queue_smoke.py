from __future__ import annotations

import argparse
import time
import uuid
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rq import Retry
from rq.job import Job

from task_queue import get_queue, get_redis


def _wait_for_job(job_id: str, *, timeout: int) -> Job:
    redis_conn = get_redis()
    deadline = time.time() + timeout
    last_status = "unknown"
    while time.time() < deadline:
        job = Job.fetch(job_id, connection=redis_conn)
        last_status = job.get_status(refresh=True)
        if last_status in {"finished", "failed", "stopped", "canceled"}:
            return job
        time.sleep(1)
    raise TimeoutError(f"Timed out waiting for {job_id}; last_status={last_status}")


def run(timeout: int = 90) -> dict:
    queue = get_queue()
    redis_conn = get_redis()
    suffix = uuid.uuid4().hex[:12]
    smoke_id = f"v3_queue_smoke_{suffix}"
    retry_id = f"v3_queue_retry_smoke_{suffix}"
    retry_key = f"v3:queue-smoke:{suffix}:attempts"
    try:
        smoke_job = queue.enqueue(
            "task_queue.run_queue_smoke_wrapper",
            {"echo": suffix},
            job_id=smoke_id,
            job_timeout=60,
        )
        retry_job = queue.enqueue(
            "task_queue.run_queue_retry_smoke_wrapper",
            retry_key,
            job_id=retry_id,
            retry=Retry(max=1, interval=[1]),
            job_timeout=60,
        )
        smoke_done = _wait_for_job(smoke_job.id, timeout=timeout)
        retry_done = _wait_for_job(retry_job.id, timeout=timeout)
        result = {
            "ok": smoke_done.is_finished and retry_done.is_finished,
            "smoke_status": smoke_done.get_status(refresh=True),
            "retry_status": retry_done.get_status(refresh=True),
            "smoke_result": smoke_done.result,
            "retry_result": retry_done.result,
            "failed_registry_checked": retry_done.get_status(refresh=True) != "failed",
        }
        if not result["ok"]:
            raise RuntimeError(result)
        return result
    finally:
        redis_conn.delete(retry_key)
        for job_id in (smoke_id, retry_id):
            try:
                Job.fetch(job_id, connection=redis_conn).delete()
            except Exception:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a no-side-effect Redis/RQ queue smoke test.")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    result = run(timeout=args.timeout)
    print(
        "QUEUE_SMOKE_RESULT "
        f"ok={result['ok']} "
        f"smoke_status={result['smoke_status']} "
        f"retry_status={result['retry_status']} "
        f"failed_registry_checked={result['failed_registry_checked']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
