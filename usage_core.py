import json
import os
from typing import Any, Dict, Optional

from db import create_usage_event, get_usage_totals_by_stage, update_job_fields


def _float_env(name: str, default: str) -> float:
    return float(os.getenv(name, default))


PROMPT_PRICE_PER_MILLION_TOKENS_CNY = _float_env("PROMPT_PRICE_PER_MILLION_TOKENS_CNY", "8")
IMAGE_PRICE_PER_MILLION_TOKENS_CNY = _float_env("IMAGE_PRICE_PER_MILLION_TOKENS_CNY", "25")
VIDEO_PRICE_PER_MILLION_TOKENS_CNY = _float_env("VIDEO_PRICE_PER_MILLION_TOKENS_CNY", "46")
IMAGE_PRICE_PER_IMAGE_CNY = _float_env("IMAGE_PRICE_PER_IMAGE_CNY", "0.35")
IMAGE_PRICING_MODE = os.getenv("IMAGE_PRICING_MODE", "per_image")


def estimate_stage_cost(stage: str, total_tokens: int = 0, unit_count: int = 0) -> float:
    if stage == "prompt_generation":
        return round(total_tokens / 1_000_000 * PROMPT_PRICE_PER_MILLION_TOKENS_CNY, 4)
    if stage == "image_generation":
        if IMAGE_PRICING_MODE == "per_image":
            return round(unit_count * IMAGE_PRICE_PER_IMAGE_CNY, 4)
        return round(total_tokens / 1_000_000 * IMAGE_PRICE_PER_MILLION_TOKENS_CNY, 4)
    if stage == "video_generation":
        return round(total_tokens / 1_000_000 * VIDEO_PRICE_PER_MILLION_TOKENS_CNY, 4)
    return 0.0


def record_usage(
    *,
    job_id: int,
    stage: str,
    entity_type: str,
    action: str,
    entity_id: Optional[int] = None,
    model_name: Optional[str] = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: Optional[int] = None,
    estimated_cost_cny: Optional[float] = None,
    raw_usage: Optional[Dict[str, Any]] = None,
    status: str = "succeeded",
    unit_count: int = 0,
) -> int:
    computed_total = total_tokens if total_tokens is not None else input_tokens + output_tokens
    if estimated_cost_cny is None:
        estimated_cost_cny = estimate_stage_cost(stage, computed_total, unit_count)
    event_id = create_usage_event(
        {
            "job_id": job_id,
            "stage": stage,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "action": action,
            "model_name": model_name,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": computed_total,
            "estimated_cost_cny": estimated_cost_cny,
            "raw_usage_json": json.dumps(raw_usage, ensure_ascii=False) if raw_usage is not None else None,
            "status": status,
        }
    )
    refresh_job_usage_totals(job_id)
    return event_id


def refresh_job_usage_totals(job_id: int) -> None:
    totals = get_usage_totals_by_stage(job_id)
    prompt = totals.get("prompt_generation", {"total_tokens": 0, "estimated_cost_cny": 0})
    image = totals.get("image_generation", {"total_tokens": 0, "estimated_cost_cny": 0})
    video = totals.get("video_generation", {"total_tokens": 0, "estimated_cost_cny": 0})
    publish = totals.get("publishing", {"total_tokens": 0, "estimated_cost_cny": 0})
    total_tokens = int(prompt["total_tokens"] + image["total_tokens"] + video["total_tokens"] + publish["total_tokens"])
    total_cost = round(
        prompt["estimated_cost_cny"]
        + image["estimated_cost_cny"]
        + video["estimated_cost_cny"]
        + publish["estimated_cost_cny"],
        4,
    )
    update_job_fields(
        job_id,
        {
            "prompt_total_tokens": int(prompt["total_tokens"]),
            "prompt_estimated_cost_cny": round(prompt["estimated_cost_cny"], 4),
            "image_total_tokens": int(image["total_tokens"]),
            "image_estimated_cost_cny": round(image["estimated_cost_cny"], 4),
            "video_total_tokens": int(video["total_tokens"]),
            "video_estimated_cost_cny": round(video["estimated_cost_cny"], 4),
            "publish_total_tokens": int(publish["total_tokens"]),
            "publish_estimated_cost_cny": round(publish["estimated_cost_cny"], 4),
            "total_tokens": total_tokens,
            "estimated_cost_cny": total_cost,
            "total_estimated_cost_cny": total_cost,
        },
    )
