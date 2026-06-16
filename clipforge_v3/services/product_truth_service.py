from __future__ import annotations

import json

from clipforge_v3.director.intake import bool_hint, detect_geometry_measurement, normalize_description, parse_measurement_value, split_csvish
from clipforge_v3.providers.openai_text import to_json_text, validate_product_truth_json
from clipforge_v3.repositories import project_repository, shot_repository
from clipforge_v3.schemas.product_truth import ProductTruthInput, V3ProductTruthRecord


FORBIDDEN_MATERIAL_HINTS = ["wool", "felt", "synthetic fur", "grinding stone"]


def bootstrap_product_truth(*, project: dict) -> V3ProductTruthRecord:
    product_input = ProductTruthInput(
        product_name=project["product_name"],
        product_category=project["product_category"],
        source_description=project["parts_summary"] or project["product_name"] + " " + project["product_category"],
        product_url=project.get("product_url", ""),
        dimensions=project.get("dimensions_input", ""),
        materials=project.get("materials_input", ""),
        package_quantity=project.get("package_quantity", ""),
        parts_summary=project.get("parts_summary", ""),
        installation_method=project.get("installation_method", ""),
        working_surface=project.get("working_surface_input", ""),
        intended_for=project.get("intended_for", ""),
        not_for=project.get("not_for", ""),
        safety_notes=project.get("safety_notes", ""),
    )
    return save_product_truth(project_id=project["id"], product_input=product_input, approve=False, invalidate_downstream=False)


def extract_product_truth_payload(product_input: ProductTruthInput) -> dict:
    text = normalize_description(
        " ".join(
            [
                product_input.product_name,
                product_input.product_category,
                product_input.source_description,
                product_input.dimensions,
                product_input.materials,
                product_input.parts_summary,
                product_input.installation_method,
                product_input.working_surface,
                product_input.intended_for,
                product_input.not_for,
                product_input.safety_notes,
            ]
        )
    )
    components = split_csvish(product_input.parts_summary)
    if not components:
        components = split_csvish(product_input.product_name)
    colors = []
    for color in ["off-white", "white", "black", "silver", "red", "blue", "natural"]:
        if color in text.lower():
            colors.append(color)
    uncertain = []
    if "approximately" in text.lower() or "~" in text:
        uncertain.append("Some numeric counts are approximate and should not be treated as visually exact.")
    diameter = detect_geometry_measurement(text, "diameter")
    thickness = detect_geometry_measurement(text, "thickness")
    center_hole = detect_geometry_measurement(text, "center_hole")
    structured_measurements = {
        "diameter": parse_measurement_value(diameter) if diameter else {},
        "thickness": parse_measurement_value(thickness) if thickness else {},
        "center_hole": parse_measurement_value(center_hole) if center_hole else {},
    }
    payload = {
        "product_type": product_input.product_category,
        "immutable_geometry": {
            "shape": "circular wheel" if bool_hint(text, "wheel", "disc") else "",
            "diameter": diameter,
            "thickness": thickness,
            "center_hole": center_hole,
            "component_count": product_input.package_quantity or "",
            "other": [item for item in split_csvish(product_input.dimensions) if item],
        },
        "materials": {
            "correct": split_csvish(product_input.materials) or ["unknown pending confirmation"],
            "forbidden": FORBIDDEN_MATERIAL_HINTS,
        },
        "colors": colors,
        "components": components,
        "installation": {
            "required_steps": split_csvish(product_input.installation_method) or ["User confirmation required"],
            "required_relationships": [
                relation
                for relation in [
                    "spindle passes through center hole" if bool_hint(text, "spindle", "center hole") else "",
                    "fastener stack remains visible during install" if bool_hint(text, "washer", "nut", "fastener") else "",
                ]
                if relation
            ],
            "forbidden_relationships": ["Do not float components without physical support."],
        },
        "working_surface": {
            "correct": split_csvish(product_input.working_surface) or ["pending confirmation"],
            "incorrect": split_csvish(product_input.not_for),
        },
        "allowed_behaviors": [
            "Only one physically plausible action per shot.",
            "Preserve real assembly order and contact surfaces.",
        ],
        "forbidden_transformations": [
            "Do not replace the real product with an invented variant.",
            "Do not morph product material into a different category.",
        ],
        "safety_constraints": split_csvish(product_input.safety_notes) or ["Maintain safe hand placement around active tool surfaces."],
        "uncertain_facts": uncertain,
        "source_evidence": [
            {"source_type": "description", "detail": product_input.source_description},
            {"source_type": "manual_input", "detail": json.dumps({"structured_measurements": structured_measurements}, ensure_ascii=False)},
            *(
                [{"source_type": "product_url", "detail": product_input.product_url}]
                if product_input.product_url
                else []
            ),
            *(
                [{"source_type": "manual_input", "detail": product_input.dimensions}]
                if product_input.dimensions
                else []
            ),
        ],
    }
    return validate_product_truth_json(to_json_text(payload)).model_dump()


def save_product_truth(*, project_id: int, product_input: ProductTruthInput, approve: bool, invalidate_downstream: bool) -> V3ProductTruthRecord:
    payload = extract_product_truth_payload(product_input)
    version = project_repository.get_next_product_truth_version(project_id)
    record = V3ProductTruthRecord(
        project_id=project_id,
        source_description=product_input.source_description,
        product_truth_json=payload,
        user_approved=approve,
        version=version,
        invalidates_shots=invalidate_downstream,
    )
    truth_json = record.product_truth_json.model_dump()
    project_repository.create_product_truth(
        {
            "project_id": record.project_id,
            "source_description": record.source_description,
            "immutable_geometry_json": truth_json["immutable_geometry"],
            "dimensions_json": {"raw": product_input.dimensions, "structured": {
                "diameter": parse_measurement_value(truth_json["immutable_geometry"].get("diameter", "")) if truth_json["immutable_geometry"].get("diameter") else {},
                "thickness": parse_measurement_value(truth_json["immutable_geometry"].get("thickness", "")) if truth_json["immutable_geometry"].get("thickness") else {},
                "center_hole": parse_measurement_value(truth_json["immutable_geometry"].get("center_hole", "")) if truth_json["immutable_geometry"].get("center_hole") else {},
            }},
            "material_json": truth_json["materials"],
            "colors_json": {"values": truth_json["colors"]},
            "components_json": [{"name": item} for item in truth_json["components"]],
            "installation_rules_json": truth_json["installation"]["required_steps"],
            "working_surface_json": truth_json["working_surface"],
            "allowed_behaviors_json": truth_json["allowed_behaviors"],
            "forbidden_transformations_json": truth_json["forbidden_transformations"],
            "forbidden_materials_json": truth_json["materials"]["forbidden"],
            "safety_constraints_json": truth_json["safety_constraints"],
            "confidence_json": {"manual_inputs_present": 1.0 if product_input.dimensions else 0.5},
            "user_approved": approve,
            "version": version,
            "product_truth_json": truth_json,
            "invalidates_shots": invalidate_downstream,
        }
    )
    if invalidate_downstream:
        shot_repository.invalidate_unlocked_shots(project_id)
        shot_repository.delete_prompt_versions_for_project(project_id)
        project_repository.update_project(project_id, {"current_stage": "product_truth", "director_plan_status": "stale"})
    elif approve:
        project_repository.update_project(project_id, {"current_stage": "reference_assets"})
    return record


def confirm_latest_product_truth(project_id: int) -> dict:
    latest = get_latest_product_truth(project_id)
    if not latest:
        raise KeyError(f"No product truth found for project {project_id}")
    conn_payload = dict(latest)
    project_repository.create_product_truth(
        {
            "project_id": conn_payload["project_id"],
            "source_description": conn_payload["source_description"],
            "immutable_geometry_json": conn_payload["immutable_geometry_json"],
            "dimensions_json": conn_payload["dimensions_json"],
            "material_json": conn_payload["material_json"],
            "colors_json": conn_payload["colors_json"],
            "components_json": conn_payload["components_json"],
            "installation_rules_json": conn_payload["installation_rules_json"],
            "working_surface_json": conn_payload["working_surface_json"],
            "allowed_behaviors_json": conn_payload["allowed_behaviors_json"],
            "forbidden_transformations_json": conn_payload["forbidden_transformations_json"],
            "forbidden_materials_json": conn_payload["forbidden_materials_json"],
            "safety_constraints_json": conn_payload["safety_constraints_json"],
            "confidence_json": conn_payload["confidence_json"],
            "user_approved": True,
            "version": project_repository.get_next_product_truth_version(project_id),
            "product_truth_json": conn_payload["product_truth_json"],
            "invalidates_shots": False,
        }
    )
    project_repository.update_project(project_id, {"current_stage": "reference_assets"})
    return get_latest_product_truth(project_id)


def list_product_truth_versions(project_id: int) -> list[dict]:
    return [_decode_truth_row(dict(row)) for row in project_repository.list_product_truth_versions(project_id)]


def get_latest_product_truth(project_id: int) -> dict | None:
    row = project_repository.get_latest_product_truth(project_id)
    if not row:
        return None
    return _decode_truth_row(dict(row))


def _decode_truth_row(row: dict) -> dict:
    object_fields = [
        "immutable_geometry_json",
        "dimensions_json",
        "material_json",
        "colors_json",
        "working_surface_json",
        "confidence_json",
        "product_truth_json",
    ]
    list_fields = [
        "components_json",
        "installation_rules_json",
        "allowed_behaviors_json",
        "forbidden_transformations_json",
        "forbidden_materials_json",
        "safety_constraints_json",
    ]
    for field in object_fields:
        row[field] = json.loads(row[field] or "{}")
    for field in list_fields:
        row[field] = json.loads(row[field] or "[]")
    row["user_approved"] = bool(row.get("user_approved"))
    row["invalidates_shots"] = bool(row.get("invalidates_shots"))
    return row
