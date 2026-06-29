import os

V3_STAGE_SEQUENCE = [
    "product_truth",
    "assets",
    "shots",
    "generation",
    "review",
    "publish",
]


def is_v3_enabled() -> bool:
    return os.getenv("CLIPFORGE_V3_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
