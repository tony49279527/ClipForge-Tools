from __future__ import annotations

from clipforge_v3.providers.openai_text import to_json_text, validate_fidelity_json


def allocate_fidelity(*, shot_purpose: str, strict_identity: bool, action_complexity: str, crowded_scene: bool, readable_text_required: bool) -> dict:
    overloaded = strict_identity and action_complexity == "high" and crowded_scene and readable_text_required
    if "installation" in shot_purpose:
        payload = {
            "primary_spend": "product_identity",
            "secondary_spend": "material_detail",
            "economized": ["background complexity", "camera movement", "human activity"],
            "reason": "Installation requires geometry and relationship clarity over spectacle.",
            "split_recommended": overloaded or action_complexity == "high",
            "split_reason": "Separate installation proof from active use to reduce overload." if overloaded or action_complexity == "high" else "",
        }
    elif "polish" in shot_purpose or "motion" in shot_purpose:
        payload = {
            "primary_spend": "product_identity" if strict_identity else "motion_boldness",
            "secondary_spend": "material_motion",
            "economized": ["background complexity", "secondary props", "camera movement"],
            "reason": "Visible contact accuracy matters more than environmental richness.",
            "split_recommended": overloaded,
            "split_reason": "Split structure proof and action proof into separate shots." if overloaded else "",
        }
    else:
        payload = {
            "primary_spend": "product_identity",
            "secondary_spend": "material_detail",
            "economized": ["human activity", "camera movement"],
            "reason": "Hero and structure shots should prioritize stable product readability.",
            "split_recommended": overloaded,
            "split_reason": "Scene is overloaded for a single shot." if overloaded else "",
        }
    return validate_fidelity_json(to_json_text(payload)).model_dump()
