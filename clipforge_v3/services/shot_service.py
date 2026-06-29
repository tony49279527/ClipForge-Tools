from __future__ import annotations

import json

from clipforge_v3.director.fidelity_allocator import allocate_fidelity
from clipforge_v3.director.mode_router import choose_generation_mode
from clipforge_v3.director.reference_mapper import build_reference_role_map
from clipforge_v3.director.shot_planner import build_shot_contracts
from clipforge_v3.providers.openai_text import compile_shot_prompt
from clipforge_v3.repositories import project_repository, shot_repository
from clipforge_v3.services.observability_service import sanitize


def regenerate_director_plan(*, project: dict, product_truth: dict, assets: list[dict]) -> list[dict]:
    shot_repository.invalidate_unlocked_shots(project["id"])
    shot_repository.delete_prompt_versions_for_project(project["id"])
    role_maps = []
    mode_decisions = []
    fidelities = []
    plan_assets = assets
    if not plan_assets:
        plan_assets = []
    purposes = [
        "product_structure_proof",
        "installation_relationship_proof",
        "working_surface_proof",
        "result_proof",
    ]
    for purpose in purposes:
        selected_assets = select_assets_for_purpose(purpose, plan_assets)
        role_map = build_reference_role_map(shot_purpose=purpose, assets=selected_assets)
        mode = choose_generation_mode(
            shot_purpose=purpose,
            strict_identity=True,
            assets=selected_assets,
            needs_continuity=purpose != "product_structure_proof",
            extends_previous=purpose == "result_proof",
        )
        fidelity = allocate_fidelity(
            shot_purpose=purpose,
            strict_identity=True,
            action_complexity="high" if purpose == "working_surface_proof" else "medium",
            crowded_scene=False,
            readable_text_required=False,
        )
        role_maps.append(role_map)
        mode_decisions.append(mode)
        fidelities.append(fidelity)
    contracts = build_shot_contracts(
        product_name=project["product_name"],
        product_type=product_truth["product_truth_json"]["product_type"],
        truth=product_truth,
        role_maps=role_maps,
        mode_decisions=mode_decisions,
        fidelities=fidelities,
    )
    created = []
    for index, contract in enumerate(contracts, start=1):
        shot_db_id = shot_repository.create_shot(
            {
                "project_id": project["id"],
                "sequence_index": index,
                "purpose": contract["purpose"],
                "commercial_beat": contract["commercial_beat"],
                "duration": contract["duration"],
                "mode": contract["mode"],
                "primary_spend": contract["primary_spend"],
                "secondary_spend": contract.get("secondary_spend"),
                "economized_json": contract["economized"],
                "subject_action": contract["subject_action"],
                "single_visible_beat": contract["single_visible_beat"],
                "start_state_json": contract["start_state"],
                "end_state_json": contract["end_state"],
                "camera_contract_json": contract["camera_contract"],
                "lighting_contract_json": contract["lighting_contract"],
                "audio_contract_json": contract["audio_contract"],
                "reference_roles_json": contract["reference_roles"],
                "continuity_anchors_json": contract["continuity_anchors"],
                "continuity_group": "assembly_chain" if contract["purpose"] != "product_structure_proof" else "product_identity",
                "constraints_json": contract["constraints"],
                "risk_codes_json": contract["risk_codes"],
                "generation_strategy": contract["generation_strategy"],
                "depends_on_shot_id": contract["depends_on_shot_id"],
                "status": "planned" if not mode_decisions[index - 1]["missing_assets"] else "blocked_missing_assets",
                "user_approved": False,
                "version": 1,
                "shot_id": contract["shot_id"],
                "mode_decision_json": mode_decisions[index - 1],
                "fidelity_json": fidelities[index - 1],
                "locked_by_user": False,
            }
        )
        row = shot_repository.get_shot(shot_db_id)
        decoded = _decode_shot_row(dict(row))
        prompt = compile_shot_prompt(shot=decoded, product_truth=product_truth, project=project)
        shot_repository.create_prompt_version(
            {
                "shot_id": shot_db_id,
                "version": 1,
                "mode": prompt.mode,
                "prompt_text": prompt.prompt_text,
                "prompt_char_count": len(prompt.prompt_text),
                "prompt_language": project["language"],
                "role_map_json": prompt.role_map,
                "compiler_warnings_json": prompt.warnings,
                "validation_result_json": prompt.validation_result,
            }
        )
        created.append(decoded)
    project_repository.update_project(project["id"], {"current_stage": "shot_contracts", "director_plan_status": "ready"})
    return created


def select_assets_for_purpose(purpose: str, assets: list[dict]) -> list[dict]:
    if purpose == "product_structure_proof":
        preferred = {"product_identity", "product_geometry", "fact_evidence"}
    elif purpose == "installation_relationship_proof":
        preferred = {"installation", "product_geometry", "product_identity", "fact_evidence"}
    elif purpose == "working_surface_proof":
        preferred = {"product_identity", "material_detail", "motion", "fact_evidence"}
    else:
        preferred = {"product_identity", "last_frame", "environment", "style"}
    chosen = [asset for asset in assets if asset["primary_role"] in preferred]
    return chosen or assets[:2]


def list_shots(project_id: int) -> list[dict]:
    return [_decode_shot_row(dict(row)) for row in shot_repository.list_shots(project_id)]


def list_prompt_versions(project_id: int) -> list[dict]:
    rows = []
    for row in shot_repository.list_prompt_versions(project_id):
        payload = dict(row)
        payload["role_map_json"] = json.loads(payload["role_map_json"] or "{}")
        payload["compiler_warnings_json"] = json.loads(payload["compiler_warnings_json"] or "[]")
        payload["validation_result_json"] = json.loads(payload["validation_result_json"] or "{}")
        if "removed_items_json" in payload:
            payload["removed_items_json"] = json.loads(payload["removed_items_json"] or "[]")
        if "provider_payload_json" in payload:
            payload["provider_payload_json"] = sanitize(json.loads(payload["provider_payload_json"] or "{}"))
        rows.append(payload)
    return rows


def confirm_all_shots(project_id: int) -> None:
    for shot in list_shots(project_id):
        shot_repository.update_shot(shot["id"], {"user_approved": 1, "locked_by_user": 1, "status": "approved"})
    project_repository.update_project(project_id, {"current_stage": "prompt_compilation"})


def create_manual_shot(project_id: int) -> None:
    shots = list_shots(project_id)
    next_index = len(shots) + 1
    shot_repository.create_shot(
        {
            "project_id": project_id,
            "sequence_index": next_index,
            "shot_id": f"S{next_index:02d}",
            "purpose": "manual_insert",
            "commercial_beat": "Manually added shot.",
            "duration": 5,
            "mode": "I2V",
            "primary_spend": "product_identity",
            "secondary_spend": "material_detail",
            "economized_json": ["background complexity"],
            "subject_action": "Manual shot action to be refined by user.",
            "single_visible_beat": "One manual beat.",
            "start_state_json": {},
            "end_state_json": {},
            "camera_contract_json": {"movement": "locked"},
            "lighting_contract_json": {},
            "audio_contract_json": {},
            "reference_roles_json": [],
            "continuity_anchors_json": {},
            "continuity_group": "manual_group",
            "constraints_json": ["Only one main visible action."],
            "risk_codes_json": ["manual_unreviewed"],
            "generation_strategy": "parallel",
            "depends_on_shot_id": shots[-1]["shot_id"] if shots else None,
            "status": "draft_manual",
            "mode_decision_json": {},
            "fidelity_json": {},
            "locked_by_user": False,
        }
    )


def copy_shot(project_id: int, shot_db_id: int) -> int:
    source = shot_repository.get_shot(shot_db_id)
    if not source:
        raise KeyError(f"Shot {shot_db_id} not found")
    shot = _decode_shot_row(dict(source))
    next_index = len(list_shots(project_id)) + 1
    return shot_repository.create_shot(
        {
            "project_id": project_id,
            "sequence_index": next_index,
            "shot_id": f"S{next_index:02d}",
            "purpose": f"{shot['purpose']}_copy",
            "commercial_beat": shot.get("commercial_beat", ""),
            "duration": shot["duration"],
            "mode": shot["mode"],
            "primary_spend": shot["primary_spend"],
            "secondary_spend": shot.get("secondary_spend"),
            "economized_json": shot["economized_json"],
            "subject_action": shot["subject_action"],
            "single_visible_beat": shot["single_visible_beat"],
            "start_state_json": shot["start_state_json"],
            "end_state_json": shot["end_state_json"],
            "camera_contract_json": shot["camera_contract_json"],
            "lighting_contract_json": shot["lighting_contract_json"],
            "audio_contract_json": shot["audio_contract_json"],
            "reference_roles_json": shot["reference_roles_json"],
            "continuity_anchors_json": shot["continuity_anchors_json"],
            "continuity_group": shot.get("continuity_group") or "copy_group",
            "constraints_json": shot["constraints_json"],
            "risk_codes_json": list(shot["risk_codes_json"]) + ["copied_requires_review"],
            "generation_strategy": shot["generation_strategy"],
            "depends_on_shot_id": shot.get("depends_on_shot_id"),
            "status": "draft_manual",
            "mode_decision_json": shot["mode_decision_json"],
            "fidelity_json": shot["fidelity_json"],
            "locked_by_user": False,
            "user_approved": False,
        }
    )


def split_shot(project_id: int, shot_db_id: int) -> int:
    source = shot_repository.get_shot(shot_db_id)
    if not source:
        raise KeyError(f"Shot {shot_db_id} not found")
    shot = _decode_shot_row(dict(source))
    half_duration = max(4, int(round(float(shot["duration"]) / 2)))
    shot_repository.update_shot(
        shot_db_id,
        {
            "duration": half_duration,
            "status": "draft_manual",
            "locked_by_user": 0,
            "user_approved": 0,
            "risk_codes_json": list(shot["risk_codes_json"]) + ["split_requires_review"],
            "single_visible_beat": f"First half of: {shot['single_visible_beat']}",
        },
    )
    next_index = len(list_shots(project_id)) + 1
    new_id = shot_repository.create_shot(
        {
            "project_id": project_id,
            "sequence_index": next_index,
            "shot_id": f"S{next_index:02d}",
            "purpose": f"{shot['purpose']}_split",
            "commercial_beat": shot.get("commercial_beat", ""),
            "duration": half_duration,
            "mode": shot["mode"],
            "primary_spend": shot["primary_spend"],
            "secondary_spend": shot.get("secondary_spend"),
            "economized_json": shot["economized_json"],
            "subject_action": f"Continue only the second half of: {shot['subject_action']}",
            "single_visible_beat": f"Second half of: {shot['single_visible_beat']}",
            "start_state_json": shot["end_state_json"],
            "end_state_json": shot["end_state_json"],
            "camera_contract_json": shot["camera_contract_json"],
            "lighting_contract_json": shot["lighting_contract_json"],
            "audio_contract_json": shot["audio_contract_json"],
            "reference_roles_json": shot["reference_roles_json"],
            "continuity_anchors_json": shot["continuity_anchors_json"],
            "continuity_group": shot.get("continuity_group") or "split_group",
            "constraints_json": list(shot["constraints_json"]) + ["This split shot may change only one visible beat."],
            "risk_codes_json": list(shot["risk_codes_json"]) + ["split_requires_review"],
            "generation_strategy": "sequential",
            "depends_on_shot_id": shot["shot_id"],
            "status": "draft_manual",
            "mode_decision_json": shot["mode_decision_json"],
            "fidelity_json": shot["fidelity_json"],
            "locked_by_user": False,
            "user_approved": False,
        }
    )
    shot_repository.renumber_shots(project_id)
    return new_id


def disable_shot(shot_db_id: int) -> None:
    shot_repository.update_shot(shot_db_id, {"status": "disabled", "locked_by_user": 0, "user_approved": 0})


def update_shot_fields(shot_id: int, fields: dict) -> None:
    shot_repository.update_shot(shot_id, fields)


def delete_shot(shot_id: int, project_id: int) -> None:
    shot_repository.delete_shot(shot_id)
    shot_repository.renumber_shots(project_id)


def move_shot(project_id: int, shot_id: int, direction: str) -> None:
    shot_repository.swap_shot_order(project_id, shot_id, direction)
    shot_repository.renumber_shots(project_id)


def _decode_shot_row(row: dict) -> dict:
    json_fields = [
        "economized_json",
        "start_state_json",
        "end_state_json",
        "camera_contract_json",
        "lighting_contract_json",
        "audio_contract_json",
        "reference_roles_json",
        "continuity_anchors_json",
        "constraints_json",
        "risk_codes_json",
        "mode_decision_json",
        "fidelity_json",
    ]
    for field in json_fields:
        default = "{}" if field.endswith("contract_json") or field.endswith("state_json") or field.endswith("_json") and field in {"continuity_anchors_json", "mode_decision_json", "fidelity_json"} else "[]"
        row[field] = json.loads(row[field] or default)
    row["user_approved"] = bool(row.get("user_approved"))
    row["locked_by_user"] = bool(row.get("locked_by_user"))
    row["selected_take_id"] = row.get("selected_take_id")
    return row
