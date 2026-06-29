from __future__ import annotations

import os
from datetime import datetime

from clipforge_v3.providers.config import SEEDANCE_DEFAULT_RESOLUTION
from clipforge_v3.providers.seedance_ark import get_provider


PRICE_UPDATED_AT = os.getenv("V3_PRICE_UPDATED_AT", "2026-06-16")
PRICE_SOURCE = os.getenv("V3_PRICE_SOURCE", "estimate")


def estimate_next_video_cost(duration: int, resolution: str | None = None) -> float:
    return get_provider().estimate_cost(duration=duration, resolution=resolution or SEEDANCE_DEFAULT_RESOLUTION)


def build_cost_center(*, project: dict, shots: list[dict], takes: list[dict], usage_events: list[dict] | None = None) -> dict:
    usage_events = usage_events or []
    take_cost_total = round(sum(float(take.get("estimated_cost") or 0) for take in takes), 4)
    prompt_cost = round(sum(float(event.get("estimated_cost") or 0) for event in usage_events if "prompt" in (event.get("stage") or "")), 4)
    publish_cost = round(sum(float(event.get("estimated_cost") or 0) for event in usage_events if "publish" in (event.get("stage") or "")), 4)
    draft_cost = round(sum(float(take.get("estimated_cost") or 0) for take in takes if take.get("tier") == "draft"), 4)
    production_cost = round(sum(float(take.get("estimated_cost") or 0) for take in takes if take.get("tier") == "production"), 4)
    retake_cost = round(sum(float(take.get("estimated_cost") or 0) for take in takes if take.get("parent_take_id")), 4)
    budget = float(project.get("max_cost_cny") or 0)
    remaining = round(max(budget - take_cost_total - prompt_cost - publish_cost, 0), 4) if budget else None
    percent = round(((take_cost_total + prompt_cost + publish_cost) / budget) * 100, 2) if budget else None
    per_shot = []
    for shot in shots:
        shot_takes = [take for take in takes if take.get("shot_id") == shot["id"]]
        next_cost = estimate_next_video_cost(shot["duration"], project.get("resolution"))
        per_shot.append(
            {
                "shot_id": shot["shot_id"],
                "current_cost": round(sum(float(take.get("estimated_cost") or 0) for take in shot_takes), 4),
                "take_count": len(shot_takes),
                "estimated_next_cost": next_cost,
            }
        )
    return {
        "currency": "CNY",
        "price_source": PRICE_SOURCE,
        "price_updated_at": PRICE_UPDATED_AT,
        "is_estimate": True,
        "prompt_cost": prompt_cost,
        "storyboard_cost": 0,
        "draft_video_cost": draft_cost,
        "production_video_cost": production_cost,
        "retake_cost": retake_cost,
        "publish_cost": publish_cost,
        "project_total_cost": round(take_cost_total + prompt_cost + publish_cost, 4),
        "budget": budget,
        "budget_remaining": remaining,
        "budget_percent": percent,
        "per_shot": per_shot,
        "updated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    }

