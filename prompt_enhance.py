import os
from typing import Optional

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-5-mini")

_SYSTEM = (
    "You are a professional video ad prompt engineer for AI video generation models like Seedance. "
    "Transform a static storyboard frame description into a rich, motion-oriented video generation prompt. "
    "Keep the prompt under 200 words, highly visual, and optimized for short-form video ads. "
    "Include: dynamic camera movement, lighting, mood, and temporal progression. "
    "Output ONLY the prompt text. No JSON, no markdown, no explanation."
)


def enhance_video_prompt(
    original_prompt: str,
    product_name: str,
    scene_role: str,
    clip_duration: int,
    ratio: str,
) -> str:
    if not os.getenv("OPENAI_API_KEY") or OpenAI is None:
        return original_prompt

    client = OpenAI()
    user_prompt = (
        f"Product: {product_name}\n"
        f"Scene role: {scene_role}\n"
        f"Duration: {clip_duration}s, Ratio: {ratio}\n"
        f"Original storyboard description:\n{original_prompt}\n\n"
        f"Transform into a cinematic video generation prompt."
    )
    response = client.responses.create(model=TEXT_MODEL, system=_SYSTEM, input=user_prompt)
    enhanced = (getattr(response, "output_text", "") or "").strip()
    return enhanced or original_prompt
