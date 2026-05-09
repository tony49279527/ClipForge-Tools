import json
import os
from typing import Any, Dict, List

from video_core import STYLE_PREFIX

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional dependency until installed
    OpenAI = None


TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-5-mini")


def _default_scene_roles(clip_count: int) -> List[str]:
    if clip_count == 1:
        return ["before_after"]
    if clip_count == 2:
        return ["hook_closeup", "demo_result"]
    if clip_count == 4:
        return ["hook", "closeup", "setup", "result"]
    roles = []
    for index in range(1, clip_count + 1):
        roles.append(f"scene_{index}")
    return roles


def _fallback_prompts(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    clip_count = int(payload["clip_count"])
    roles = _default_scene_roles(clip_count)
    results: List[Dict[str, Any]] = []
    for index, role in enumerate(roles, start=1):
        prompt_zh = (
            f"{STYLE_PREFIX} 第 {index} 段，场景角色 {role}。产品：{payload['product_name']}。"
            f"核心想法：{payload['simple_idea']}。目标用户：{payload.get('target_audience') or 'Amazon 与 YouTube 工具内容用户'}。"
            f"请让镜头重点清晰、结构明确、动作真实。"
        )
        prompt_en = (
            f"Real American garage tool commercial scene, role={role}. Product: {payload['product_name']}. "
            f"Idea: {payload['simple_idea']}. Audience: {payload.get('target_audience') or 'Amazon and YouTube tool shoppers'}. "
            f"Show a clear, realistic, instructional frame with stable composition, real hands only, no face, "
            f"and strong structure visibility."
        )
        results.append(
            {
                "clip_index": index,
                "scene_role": role,
                "prompt_zh": prompt_zh,
                "prompt_en": prompt_en,
            }
        )
    return results


def generate_storyboard_prompts(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not os.getenv("OPENAI_API_KEY") or OpenAI is None:
        return {"frames": _fallback_prompts(payload), "usage": {}}

    client = OpenAI()
    prompt = f"""
You are designing storyboard prompts for a tool product video workflow.
Return JSON only with the shape:
{{
  "frames": [
    {{
      "clip_index": 1,
      "scene_role": "hook",
      "prompt_zh": "...",
      "prompt_en": "..."
    }}
  ]
}}

Rules:
- Generate exactly {payload["clip_count"]} frames.
- Chinese and English prompts must describe the same frame.
- English prompt should work well for image generation.
- Product: {payload["product_name"]}
- Video mode: {payload["video_mode"]}
- Ratio: {payload["ratio"]}
- Audience: {payload.get("target_audience") or "Amazon and YouTube tool buyers"}
- User idea: {payload["simple_idea"]}
- Style preference: {payload.get("style_preference") or "真实美国车库五金工具广告风格"}
- Keep each frame visually specific and suitable as a storyboard image.
"""
    response = client.responses.create(model=TEXT_MODEL, input=prompt)
    text = getattr(response, "output_text", "") or ""
    usage = getattr(response, "usage", None)
    parsed = json.loads(text)
    return {"frames": parsed["frames"], "usage": usage.model_dump() if usage and hasattr(usage, "model_dump") else {}}
