"""
Task Queue — Redis / RQ based background job execution for ClipForge Tools.

Replaces the previous daemon-thread approach with a proper task queue:
  - Persistent queue (survives app restarts)
  - Configurable concurrency (via RQ worker count)
  - Built-in retry for failed jobs
  - Job-level and clip-level parallelism
"""

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from redis import Redis
from rq import Queue, Retry
from rq.job import Job as RQJob

from clipforge_v3.services.maintenance_service import assert_writes_allowed
from clipforge_v3.services.queue_config import resolve_redis_settings

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

QUEUE_NAME = os.getenv("RQ_QUEUE_NAME", "clipforge").strip() or "clipforge"

# Concurrency limits
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "3"))
MAX_CLIP_WORKERS = int(os.getenv("MAX_CLIP_WORKERS", "4"))  # clips per job in parallel
MAX_RETRIES = int(os.getenv("JOB_RETRIES", "2"))
RETRY_DELAY_SEC = int(os.getenv("JOB_RETRY_DELAY", "5"))

# ---------------------------------------------------------------------------
# Redis / RQ setup
# ---------------------------------------------------------------------------

_redis_client: Optional[Redis] = None
_queue: Optional[Queue] = None
STATIC_JOB_PREFIXES = ("video", "prompts", "storyboard_video", "publish", "v3_bootstrap", "v3_generation")


def _enqueue_with_reusable_job_id(*, job_id: str, func: str, args: List[Any], job_timeout: int) -> RQJob:
    """Enqueue with a stable job id, reusing active jobs and replacing finished ones."""
    assert_writes_allowed()
    q = get_queue()
    redis_conn = get_redis()

    try:
        existing_job = RQJob.fetch(job_id, connection=redis_conn)
        existing_status = existing_job.get_status(refresh=False)
        if existing_status in {"queued", "started", "deferred", "scheduled"}:
            return existing_job
        try:
            existing_job.delete()
        except Exception:
            pass
    except Exception:
        pass

    return q.enqueue(
        func,
        *args,
        retry=Retry(max=MAX_RETRIES, interval=[RETRY_DELAY_SEC]),
        job_timeout=job_timeout,
        job_id=job_id,
    )


def get_redis() -> Redis:
    global _redis_client
    if _redis_client is None:
        settings = resolve_redis_settings()
        _redis_client = Redis.from_url(settings.redis_url, decode_responses=False)
    return _redis_client


def get_queue() -> Queue:
    global _queue
    if _queue is None:
        settings = resolve_redis_settings()
        _queue = Queue(settings.queue_name, connection=get_redis(), default_timeout=3600)
    return _queue


# ---------------------------------------------------------------------------
# Job enqueue helpers
# ---------------------------------------------------------------------------

def enqueue_video_job(job_id: int) -> RQJob:
    """Enqueue a 1.0 video generation job."""
    return _enqueue_with_reusable_job_id(
        job_id=f"video_{job_id}",
        func="task_queue.run_video_job_wrapper",
        args=[job_id],
        job_timeout=3600,
    )


def enqueue_storyboard_prompts_job(job_id: int) -> RQJob:
    """Enqueue a 2.0 storyboard prompt generation job."""
    return _enqueue_with_reusable_job_id(
        job_id=f"prompts_{job_id}",
        func="task_queue.run_storyboard_prompts_wrapper",
        args=[job_id],
        job_timeout=600,
    )


def enqueue_storyboard_images_job(job_id: int, base_url: str, frame_id: int = None) -> RQJob:
    """Enqueue a 2.0 storyboard image generation job."""
    return _enqueue_with_reusable_job_id(
        job_id=f"images_{job_id}_{frame_id or 'all'}",
        func="task_queue.run_storyboard_images_wrapper",
        args=[job_id, base_url, frame_id],
        job_timeout=1800,
    )


def enqueue_storyboard_video_job(job_id: int) -> RQJob:
    """Enqueue a 2.0 video generation job (from approved storyboard)."""
    return _enqueue_with_reusable_job_id(
        job_id=f"storyboard_video_{job_id}",
        func="task_queue.run_storyboard_video_wrapper",
        args=[job_id],
        job_timeout=3600,
    )


def enqueue_publish_job(job_id: int) -> RQJob:
    """Enqueue a YouTube publish job."""
    return _enqueue_with_reusable_job_id(
        job_id=f"publish_{job_id}",
        func="task_queue.run_publish_wrapper",
        args=[job_id],
        job_timeout=600,
    )


def enqueue_v3_bootstrap_job(project_id: int) -> RQJob:
    """Enqueue a ClipForge 3.0 bootstrap/status refresh task."""
    return _enqueue_with_reusable_job_id(
        job_id=f"v3_bootstrap_{project_id}",
        func="task_queue.run_v3_bootstrap_wrapper",
        args=[project_id],
        job_timeout=600,
    )


def enqueue_v3_generation_job(submission_id: int, idempotency_key: str) -> RQJob:
    """Enqueue a provider-backed ClipForge 3.0 generation task.

    The RQ job id is stable per idempotency key so worker retries or repeated
    clicks resume the same provider submission instead of creating another paid
    task.
    """
    return _enqueue_with_reusable_job_id(
        job_id=f"v3_generation_{idempotency_key}",
        func="task_queue.run_v3_generation_wrapper",
        args=[submission_id],
        job_timeout=7200,
    )


# ---------------------------------------------------------------------------
# Wrapper functions (called by RQ worker)
# ---------------------------------------------------------------------------

def run_video_job_wrapper(job_id: int) -> Dict[str, Any]:
    """Wrapper that runs the 1.0 video job with parallel clip generation."""
    assert_writes_allowed()
    from video_core import ensure_runtime_dirs, run_video_job_parallel
    ensure_runtime_dirs()
    return run_video_job_parallel(job_id)


def run_storyboard_prompts_wrapper(job_id: int) -> Dict[str, Any]:
    """Wrapper for storyboard prompt generation."""
    assert_writes_allowed()
    from app import generate_storyboard_prompts_job as _run
    _run(job_id)
    return {"job_id": job_id, "stage": "prompts_ready"}


def run_storyboard_images_wrapper(job_id: int, base_url: str, frame_id: int = None) -> Dict[str, Any]:
    """Wrapper for storyboard image generation."""
    assert_writes_allowed()
    from app import generate_storyboard_images_job as _run
    _run(job_id, base_url, frame_id)
    return {"job_id": job_id, "stage": "images_ready"}


def run_storyboard_video_wrapper(job_id: int) -> Dict[str, Any]:
    """Wrapper for 2.0 storyboard video generation."""
    assert_writes_allowed()
    from video_core import ensure_runtime_dirs, run_storyboard_video_job_parallel
    ensure_runtime_dirs()
    return run_storyboard_video_job_parallel(job_id)


def run_publish_wrapper(job_id: int) -> Dict[str, Any]:
    """Wrapper for YouTube publishing."""
    assert_writes_allowed()
    from app import publish_v2_job as _run
    _run(job_id)
    return {"job_id": job_id, "stage": "published"}


def run_v3_bootstrap_wrapper(project_id: int) -> Dict[str, Any]:
    """Wrapper for v3 service tasks without importing app.py."""
    assert_writes_allowed()
    from clipforge_v3.tasks import bootstrap_project_task

    return bootstrap_project_task(project_id)


def run_v3_generation_wrapper(submission_id: int) -> Dict[str, Any]:
    """Wrapper for v3 provider generation without importing app.py."""
    assert_writes_allowed()
    from clipforge_v3.tasks import run_generation_submission_task

    return run_generation_submission_task(submission_id)


def run_queue_smoke_wrapper(payload: Dict[str, Any]) -> Dict[str, Any]:
    """No-side-effect queue smoke task for production candidate validation."""
    return {"ok": True, "echo": payload.get("echo"), "queue": QUEUE_NAME}


def run_queue_retry_smoke_wrapper(redis_key: str) -> Dict[str, Any]:
    """Fail once, then succeed, using only a temporary Redis key."""
    redis_conn = get_redis()
    attempts = int(redis_conn.incr(redis_key))
    if attempts == 1:
        raise RuntimeError("intentional queue smoke retry")
    redis_conn.delete(redis_key)
    return {"ok": True, "attempts": attempts}


# ---------------------------------------------------------------------------
# Job status helpers (for polling from the web app)
# ---------------------------------------------------------------------------

def _list_rq_job_ids(job_id: int) -> List[str]:
    """Collect all RQ job ids related to a ClipForge job."""
    redis_conn = get_redis()
    job_ids = [f"{prefix}_{job_id}" for prefix in STATIC_JOB_PREFIXES]

    for key in redis_conn.scan_iter(match=f"rq:job:images_{job_id}_*"):
        if isinstance(key, bytes):
            key = key.decode("utf-8")
        if isinstance(key, str) and key.startswith("rq:job:"):
            job_ids.append(key[len("rq:job:") :])

    seen = set()
    deduped: List[str] = []
    for current_job_id in job_ids:
        if current_job_id not in seen:
            seen.add(current_job_id)
            deduped.append(current_job_id)
    return deduped


def _fetch_related_jobs(job_id: int) -> List[RQJob]:
    redis_conn = get_redis()
    jobs: List[RQJob] = []
    for rq_job_id in _list_rq_job_ids(job_id):
        try:
            jobs.append(RQJob.fetch(rq_job_id, connection=redis_conn))
        except Exception:
            continue
    return jobs


def _job_activity_timestamp(rq_job: RQJob) -> datetime:
    return (
        rq_job.ended_at
        or rq_job.started_at
        or rq_job.enqueued_at
        or rq_job.created_at
        or datetime.min
    )


def get_job_status(job_id: int) -> Optional[Dict[str, Any]]:
    """Return the most recent queue status for a ClipForge job."""
    jobs = _fetch_related_jobs(job_id)
    if not jobs:
        return None

    rq_job = max(jobs, key=_job_activity_timestamp)
    return {
        "rq_job_id": rq_job.id,
        "status": rq_job.get_status(),
        "result": rq_job.result,
        "meta": rq_job.meta,
        "enqueued_at": str(rq_job.enqueued_at) if rq_job.enqueued_at else None,
        "started_at": str(rq_job.started_at) if rq_job.started_at else None,
        "ended_at": str(rq_job.ended_at) if rq_job.ended_at else None,
    }


def clean_job(job_id: int) -> None:
    """Remove RQ job data for a given job_id."""
    for rq_job in _fetch_related_jobs(job_id):
        try:
            rq_job.delete()
        except Exception:
            continue
