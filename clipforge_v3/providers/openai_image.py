from __future__ import annotations


def build_storyboard_brief(*, shot: dict, product_truth: dict, project: dict) -> dict:
    return {
        "title": f"{project['product_name']} storyboard {shot['shot_id']}",
        "brief": (
            f"{shot['purpose']} | Action: {shot['subject_action']} | "
            f"Surface: {product_truth['working_surface_json']} | "
            f"Camera: {shot['camera_contract_json']}"
        ),
        "identity_anchor": project["product_name"],
    }
