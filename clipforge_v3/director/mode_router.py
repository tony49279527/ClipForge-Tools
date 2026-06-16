from __future__ import annotations

from clipforge_v3.providers.openai_text import to_json_text, validate_mode_decision_json


def choose_generation_mode(*, shot_purpose: str, strict_identity: bool, assets: list[dict], needs_continuity: bool, extends_previous: bool = False, edits_existing: bool = False) -> dict:
    roles = {asset["primary_role"] for asset in assets}
    has_identity = "product_identity" in roles
    has_motion = "motion" in roles
    has_first = "first_frame" in roles
    has_last = "last_frame" in roles
    has_video = any(asset["asset_type"] == "video" for asset in assets)
    missing_assets: list[str] = []

    if edits_existing and has_video:
        selected = "edit"
        alternative = "V2V"
        required = ["existing_editable_video"]
    elif extends_previous and has_last:
        selected = "extend"
        alternative = "FLF2V"
        required = ["last_frame"]
    elif has_first and has_last and strict_identity:
        selected = "FLF2V"
        alternative = "I2V"
        required = ["first_frame", "last_frame", "product_identity"]
    elif has_video and strict_identity:
        selected = "R2V"
        alternative = "I2V"
        required = ["reference_video", "product_identity"]
    elif has_identity:
        selected = "I2V"
        alternative = "R2V" if has_video else "T2V"
        required = ["product_identity"]
    else:
        selected = "T2V"
        alternative = "I2V"
        required = ["product_identity"]
        missing_assets.append("product_identity")

    if "installation" in shot_purpose and "installation" not in roles:
        missing_assets.append("installation")
    if "polish" in shot_purpose or "motion" in shot_purpose:
        if has_motion:
            alternative = "R2V"
        elif "motion" not in roles and selected == "T2V":
            missing_assets.append("motion")

    risk = "high" if strict_identity and not has_identity else "medium"
    if selected == "T2V" and strict_identity:
        risk = "high"
    if not missing_assets and has_identity and not has_motion and "installation" not in shot_purpose:
        risk = "low"

    payload = {
        "selected_mode": selected,
        "reason": f"Selected {selected} based on available reference roles {sorted(roles)} and purpose {shot_purpose}.",
        "alternative_mode": alternative,
        "required_assets": required,
        "missing_assets": sorted(set(missing_assets)),
        "continuity_required": needs_continuity,
        "generation_strategy": "sequential" if needs_continuity or selected in {"extend", "FLF2V", "edit"} else "parallel",
        "risk_level": risk,
    }
    return validate_mode_decision_json(to_json_text(payload)).model_dump()
