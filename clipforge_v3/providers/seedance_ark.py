from __future__ import annotations

import json
import os
from typing import Any

import requests

from clipforge_v3.providers.base_adapter import VideoGenerationProvider
from clipforge_v3.providers.config import (
    SEEDANCE_BASE_URL,
    SEEDANCE_DEFAULT_RESOLUTION,
    SEEDANCE_GENERATE_AUDIO,
    SEEDANCE_MODEL,
    SEEDANCE_WATERMARK,
)


SUPPORTED_MODES = {"T2V", "I2V", "R2V", "FLF2V", "V2V", "edit", "extend"}
FAIL_CLOSED_ROLES = {"product_identity", "product_geometry", "installation", "first_frame", "last_frame", "continuity_anchor"}
FAIL_OPEN_ROLES = {"style", "environment", "audio"}


def _sanitize(obj: Any) -> Any:
    if isinstance(obj, dict):
        sanitized = {}
        for key, value in obj.items():
            if "key" in key.lower() or "authorization" in key.lower():
                sanitized[key] = "***"
            else:
                sanitized[key] = _sanitize(value)
        return sanitized
    if isinstance(obj, list):
        return [_sanitize(item) for item in obj]
    return obj


class ArkSeedanceProvider(VideoGenerationProvider):
    def validate_capabilities(self, *, mode: str, reference_roles: list[dict]) -> dict[str, Any]:
        missing = []
        unsupported = []
        if mode not in SUPPORTED_MODES:
            unsupported.append(f"mode:{mode}")
        roles = {role["primary_role"] for role in reference_roles}
        if mode in {"I2V", "R2V", "FLF2V"} and "product_identity" not in roles:
            missing.append("product_identity")
        if mode == "FLF2V":
            for needed in ("first_frame", "last_frame"):
                if needed not in roles:
                    missing.append(needed)
        return {"supported": not unsupported and not missing, "unsupported": unsupported, "missing": sorted(set(missing))}

    def build_payload(self, *, prompt_text: str, mode: str, ratio: str, duration: int, resolution: str, generate_audio: bool, reference_roles: list[dict]) -> dict[str, Any]:
        content = [{"type": "text", "text": prompt_text}]
        for index, ref in enumerate(reference_roles, start=1):
            if ref["primary_role"] in FAIL_OPEN_ROLES | FAIL_CLOSED_ROLES:
                content.append(
                    {
                        "type": "reference_role",
                        "label": f"Asset{index}",
                        "role": ref["primary_role"],
                        "must_transfer": ref.get("must_transfer", []),
                        "must_not_transfer": ref.get("must_not_transfer", []),
                    }
                )
            else:
                raise ValueError(f"Unsupported reference role for provider payload: {ref['primary_role']}")
        return {
            "model": SEEDANCE_MODEL,
            "mode": mode,
            "content": content,
            "ratio": ratio,
            "duration": duration,
            "resolution": resolution or SEEDANCE_DEFAULT_RESOLUTION,
            "generate_audio": generate_audio if generate_audio is not None else SEEDANCE_GENERATE_AUDIO,
            "watermark": SEEDANCE_WATERMARK,
        }

    def submit_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        api_key = os.getenv("ARK_API_KEY")
        if not api_key:
            raise RuntimeError("ARK_API_KEY is not configured")
        response = requests.post(
            f"{SEEDANCE_BASE_URL}/contents/generations/tasks",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        return {"request": _sanitize(payload), "response": _sanitize(response.json())}

    def get_task_status(self, task_id: str) -> dict[str, Any]:
        api_key = os.getenv("ARK_API_KEY")
        response = requests.get(
            f"{SEEDANCE_BASE_URL}/contents/generations/tasks/{task_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60,
        )
        response.raise_for_status()
        return _sanitize(response.json())

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        return {"task_id": task_id, "cancelled": False, "message": "Provider cancel not implemented for Ark endpoint."}

    def extract_result(self, response: dict[str, Any]) -> dict[str, Any]:
        content = response.get("content") or {}
        output = response.get("output") or {}
        return {
            "task_id": response.get("id") or response.get("task_id"),
            "status": response.get("status"),
            "video_url": content.get("video_url") or output.get("video_url"),
            "usage": response.get("usage") or {},
            "raw": _sanitize(response),
        }

    def estimate_cost(self, *, duration: int, resolution: str) -> float:
        multiplier = {"720p": 1.0, "1080p": 1.4, "4k": 2.2}.get(resolution or SEEDANCE_DEFAULT_RESOLUTION, 1.0)
        return round(duration * 0.15 * multiplier, 4)

    def normalize_error(self, error: Exception | dict[str, Any] | str) -> dict[str, Any]:
        text = error if isinstance(error, str) else json.dumps(_sanitize(error), ensure_ascii=False) if isinstance(error, dict) else str(error)
        code = "provider_error"
        if "Unsupported reference role" in text:
            code = "unsupported_reference_role"
        elif "ARK_API_KEY" in text:
            code = "missing_api_key"
        elif "404" in text:
            code = "provider_not_found"
        return {"code": code, "message": text}


def get_provider() -> ArkSeedanceProvider:
    return ArkSeedanceProvider()
