from __future__ import annotations

import os


def env_bool(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


SEEDANCE_PROVIDER = os.getenv("SEEDANCE_PROVIDER", "ark")
SEEDANCE_MODEL = os.getenv("SEEDANCE_MODEL", "doubao-seedance-2-0-260128")
SEEDANCE_BASE_URL = os.getenv("SEEDANCE_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
SEEDANCE_DEFAULT_RESOLUTION = os.getenv("SEEDANCE_DEFAULT_RESOLUTION", "720p")
SEEDANCE_GENERATE_AUDIO = env_bool("SEEDANCE_GENERATE_AUDIO", "true")
SEEDANCE_WATERMARK = env_bool("SEEDANCE_WATERMARK", "false")
SEEDANCE_PROMPT_MAX_CHARS = int(os.getenv("SEEDANCE_PROMPT_MAX_CHARS", "2000"))
