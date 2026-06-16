from __future__ import annotations

import json

from clipforge_v3.providers.base import PromptCompilation, repair_common_json_issues, validate_structured_json
from clipforge_v3.schemas.product_truth import ProductTruthPayload
from clipforge_v3.schemas.shot_contract import FidelityAllocation, ModeDecision, ShotContractPayload


def validate_product_truth_json(raw_text: str) -> ProductTruthPayload:
    return validate_structured_json(
        raw_text=raw_text,
        model_cls=ProductTruthPayload,
        repair_fn=repair_common_json_issues,
    )


def validate_mode_decision_json(raw_text: str) -> ModeDecision:
    return validate_structured_json(
        raw_text=raw_text,
        model_cls=ModeDecision,
        repair_fn=repair_common_json_issues,
    )


def validate_fidelity_json(raw_text: str) -> FidelityAllocation:
    return validate_structured_json(
        raw_text=raw_text,
        model_cls=FidelityAllocation,
        repair_fn=repair_common_json_issues,
    )


def validate_shot_contract_json(raw_text: str) -> ShotContractPayload:
    return validate_structured_json(
        raw_text=raw_text,
        model_cls=ShotContractPayload,
        repair_fn=repair_common_json_issues,
    )


def compile_shot_prompt(*, shot: dict, product_truth: dict, project: dict) -> PromptCompilation:
    warnings: list[str] = []
    if shot.get("mode_decision_json", {}).get("missing_assets"):
        warnings.append("Generation blocked until required reference assets are supplied.")
    prompt_text = (
        f"Create a commercially usable product video shot for {project['product_name']} in category "
        f"{project['product_category']}. Purpose: {shot['purpose']}. Commercial beat: {shot.get('commercial_beat', '')}. "
        f"Mode: {shot['mode']}. Primary spend: {shot['primary_spend']}. Secondary spend: {shot.get('secondary_spend') or ''}. "
        f"Single visible beat: {shot.get('single_visible_beat', '')}. Action: {shot['subject_action']}. "
        f"Start state: {shot['start_state_json']}. End state: {shot['end_state_json']}. "
        f"Camera: {shot['camera_contract_json']}. Lighting: {shot['lighting_contract_json']}. "
        f"Audio: {shot['audio_contract_json']}. Product Truth: {product_truth['product_truth_json']}. "
        f"Reference Role Map: {shot['reference_roles_json']}. Constraints: {shot['constraints_json']}."
    )
    return PromptCompilation(
        mode=shot["mode"],
        prompt_text=prompt_text,
        role_map={
            "reference_roles": shot["reference_roles_json"],
            "continuity": shot["continuity_anchors_json"],
        },
        warnings=warnings,
        validation_result={
            "has_product_truth": bool(product_truth.get("product_truth_json")),
            "char_count": len(prompt_text),
        },
    )


def to_json_text(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)
