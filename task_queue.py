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

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
QUEUE_NAME = os.getenv("RQ_QUEUE_NAME", "clipforge")

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
STATIC_JOB_PREFIXES = ("video", "prompts", "storyboard_video", "publish", "storyboard_clip")


def _enqueue_with_reusable_job_id(*, job_id: str, func: str, args: List[Any], job_timeout: int) -> RQJob:
    """Enqueue with a stable job id, reusing active jobs and replacing finished ones."""
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
        _redis_client = Redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client


def get_queue() -> Queue:
    global _queue
    if _queue is None:
        _queue = Queue(QUEUE_NAME, connection=get_redis(), default_timeout=3600)
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


def enqueue_storyboard_single_clip_job(job_id: int, clip_index: int) -> RQJob:
    """Enqueue regeneration for a single storyboard-derived clip."""
    return _enqueue_with_reusable_job_id(
        job_id=f"storyboard_clip_{job_id}_{clip_index}",
        func="task_queue.run_storyboard_single_clip_wrapper",
        args=[job_id, clip_index],
        job_timeout=1800,
    )


def enqueue_publish_job(job_id: int) -> RQJob:
    """Enqueue a YouTube publish job."""
    return _enqueue_with_reusable_job_id(
        job_id=f"publish_{job_id}",
        func="task_queue.run_publish_wrapper",
        args=[job_id],
        job_timeout=600,
    )


# ---------------------------------------------------------------------------
# Wrapper functions (called by RQ worker)
# ---------------------------------------------------------------------------

def run_video_job_wrapper(job_id: int) -> Dict[str, Any]:
    """Wrapper that runs the 1.0 video job with parallel clip generation."""
    from video_core import ensure_runtime_dirs, run_video_job_parallel
    from db import get_job_by_id

    ensure_runtime_dirs()
    result = run_video_job_parallel(job_id)
    job = get_job_by_id(job_id)
    if (isinstance(result, dict) and result.get("status") == "failed") or (job and job["status"] == "failed"):
        raise RuntimeError((job["error_message"] if job else None) or f"Video generation failed for job {job_id}")
    return result


def run_storyboard_prompts_wrapper(job_id: int) -> Dict[str, Any]:
    """Wrapper for storyboard prompt generation."""
    from app import generate_storyboard_prompts_job as _run
    from db import get_job_by_id

    _run(job_id)
    job = get_job_by_id(job_id)
    if not job or job["workflow_stage"] == "failed":
        raise RuntimeError((job["error_message"] if job else None) or f"Storyboard prompt generation failed for job {job_id}")
    return {"job_id": job_id, "stage": job["workflow_stage"]}


def run_storyboard_images_wrapper(job_id: int, base_url: str, frame_id: int = None) -> Dict[str, Any]:
    """Wrapper for storyboard image generation."""
    from app import generate_storyboard_images_job as _run
    from db import get_job_by_id

    _run(job_id, base_url, frame_id)
    job = get_job_by_id(job_id)
    if not job or job["workflow_stage"] == "failed":
        raise RuntimeError((job["error_message"] if job else None) or f"Storyboard image generation failed for job {job_id}")
    return {"job_id": job_id, "stage": job["workflow_stage"]}


def run_storyboard_video_wrapper(job_id: int) -> Dict[str, Any]:
    """Wrapper for 2.0 storyboard video generation."""
    from video_core import ensure_runtime_dirs, run_storyboard_video_job_parallel
    from db import get_job_by_id

    ensure_runtime_dirs()
    result = run_storyboard_video_job_parallel(job_id)
    job = get_job_by_id(job_id)
    if (isinstance(result, dict) and result.get("status") == "failed") or (job and job["workflow_stage"] == "failed"):
        raise RuntimeError((job["error_message"] if job else None) or f"Storyboard video generation failed for job {job_id}")
    return result


def run_storyboard_single_clip_wrapper(job_id: int, clip_index: int) -> Dict[str, Any]:
    """Wrapper for single-clip regeneration from storyboard."""
    from video_core import ensure_runtime_dirs, run_storyboard_single_clip_regeneration
    from db import get_job_by_id

    ensure_runtime_dirs()
    result = run_storyboard_single_clip_regeneration(job_id, clip_index)
    job = get_job_by_id(job_id)
    if (isinstance(result, dict) and result.get("status") == "failed") or (job and job["workflow_stage"] == "failed"):
        raise RuntimeError((job["error_message"] if job else None) or f"Storyboard clip regeneration failed for job {job_id}, clip {clip_index}")
    return result


def run_publish_wrapper(job_id: int) -> Dict[str, Any]:
    """Wrapper for YouTube publishing."""
    from app import publish_v2_job as _run
    from db import get_job_by_id

    _run(job_id)
    job = get_job_by_id(job_id)
    if not job or job["workflow_stage"] == "failed":
        raise RuntimeError((job["error_message"] if job else None) or f"YouTube publishing failed for job {job_id}")
    return {"job_id": job_id, "stage": job["workflow_stage"], "youtube_url": job["youtube_url"]}


# ---------------------------------------------------------------------------
# Job status helpers (for polling from the web app)
# ---------------------------------------------------------------------------

def _list_rq_job_ids(job_id: int) -> List[str]:
    """Collect all RQ job ids related to a ClipForge job."""
    redis_conn = get_redis()
    job_ids = [f"{prefix}_{job_id}" for prefix in STATIC_JOB_PREFIXES]

    for pattern in (f"rq:job:images_{job_id}_*", f"rq:job:storyboard_clip_{job_id}_*"):
        for key in redis_conn.scan_iter(match=pattern):
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
