import base64
import os
from pathlib import Path
from typing import Any, Dict

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - optional dependency until installed
    OpenAI = None


IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")


def image_size_for_ratio(ratio: str) -> str:
    return "1024x1536" if ratio == "9:16" else "1536x1024"


def generate_storyboard_image(prompt_en: str, ratio: str, output_path: Path) -> Dict[str, Any]:
    if not os.getenv("OPENAI_API_KEY") or OpenAI is None:
        raise RuntimeError("OPENAI_API_KEY is not configured for storyboard image generation")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    client = OpenAI()
    result = client.images.generate(
        model=IMAGE_MODEL,
        prompt=prompt_en,
        size=image_size_for_ratio(ratio),
    )
    image_base64 = result.data[0].b64_json
    output_path.write_bytes(base64.b64decode(image_base64))
    usage = getattr(result, "usage", None)
    usage_dict = usage.model_dump() if usage and hasattr(usage, "model_dump") else {}
    total_tokens = int(usage_dict.get("total_tokens") or 0)
    return {
        "model": IMAGE_MODEL,
        "output_path": output_path,
        "usage": usage_dict,
        "total_tokens": total_tokens,
    }
