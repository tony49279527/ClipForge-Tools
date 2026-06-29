from __future__ import annotations

import json

from db import get_conn

from clipforge_v3.error_codes import ERROR_CODES
from clipforge_v3.repositories import project_repository, shot_repository, take_repository
from clipforge_v3.schemas.review import RetakePlan, V3ReviewRecord


def list_reviews(project_id: int) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT r.*, s.shot_id AS business_shot_id, t.take_number
        FROM v3_reviews r
        JOIN v3_takes t ON t.id = r.take_id
        JOIN v3_shots s ON s.id = t.shot_id
        WHERE s.project_id = ?
        ORDER BY r.id DESC
        """,
        (project_id,),
    )
    rows = []
    for row in cur.fetchall():
        payload = dict(row)
        payload["error_codes_json"] = json.loads(payload["error_codes_json"] or "[]")
        payload["ai_suggestion_json"] = json.loads(payload.get("ai_suggestion_json") or "{}")
        rows.append(payload)
    conn.close()
    return rows


def review_take(payload: dict) -> dict:
    review = V3ReviewRecord(**payload)
    row_id = project_repository.create_review(review.model_dump())
    take_repository.update_take(
        review.take_id,
        {
            "review_summary_json": {
                "review_id": row_id,
                "verdict": review.verdict,
                "error_codes": review.error_codes_json,
                "scores": {
                    "product_identity": review.product_identity_score,
                    "mechanical_accuracy": review.mechanical_accuracy_score,
                    "material_accuracy": review.material_accuracy_score,
                    "motion_realism": review.motion_realism_score,
                    "camera_execution": review.camera_execution_score,
                    "continuity": review.continuity_score,
                    "commercial_usability": review.commercial_usability_score,
                    "safety": review.safety,
                },
            }
        },
    )
    return {"id": row_id, **review.model_dump()}


def list_error_codes() -> dict:
    return ERROR_CODES


def _last_two_reviews_for_shot(shot_db_id: int) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT r.*
        FROM v3_reviews r
        JOIN v3_takes t ON t.id = r.take_id
        WHERE t.shot_id = ?
        ORDER BY r.id DESC
        LIMIT 2
        """,
        (shot_db_id,),
    )
    rows = []
    for row in cur.fetchall():
        payload = dict(row)
        payload["error_codes_json"] = json.loads(payload["error_codes_json"] or "[]")
        rows.append(payload)
    conn.close()
    return rows


def plan_retake(take_id: int) -> dict:
    take = dict(take_repository.get_take(take_id))
    shot = dict(shot_repository.get_shot(take["shot_id"]))
    reviews = _last_two_reviews_for_shot(shot["id"])
    latest = reviews[0] if reviews else {"verdict": "REROLL", "error_codes_json": []}
    repeated = len(reviews) == 2 and set(reviews[0]["error_codes_json"]).intersection(reviews[1]["error_codes_json"])
    error_codes = latest["error_codes_json"]
    changed_variable = "seed"
    prompt_patch = ""
    verdict = latest["verdict"]
    requires_new_shot = False
    mode_change = None
    root_cause = "Random variation likely caused the issue."
    warnings: list[str] = []
    if repeated and verdict == "REROLL":
        verdict = "REWRITE"
        warnings.append("Same error repeated across two takes; reroll no longer allowed.")
    if "M001" in error_codes or "M002" in error_codes:
        verdict = "REWRITE"
        changed_variable = "one_prompt_clause"
        prompt_patch = "Strengthen installation relationship only: spindle passes through center hole, flat washer visible, nut secures wheel."
        root_cause = "Installation relationship is under-specified."
    elif "R002" in error_codes:
        verdict = "REWRITE"
        changed_variable = "camera_contract"
        prompt_patch = "Keep one primary camera move only."
        root_cause = "Camera instructions are overloaded."
    elif "A008" in error_codes:
        verdict = "REWRITE"
        changed_variable = "action_contract"
        requires_new_shot = True
        prompt_patch = "Split the overloaded motion into separate visible beats."
        root_cause = "Motion complexity is too high for one shot."
    elif latest["verdict"] == "EDIT":
        changed_variable = "one_prompt_clause"
        root_cause = "Single-layer defect can be fixed with controlled edit."
    plan = RetakePlan(
        verdict=verdict,
        root_cause=root_cause,
        changed_variable=changed_variable,
        prompt_patch=prompt_patch,
        reference_change=None,
        mode_change=mode_change,
        requires_new_shot=requires_new_shot,
        estimated_next_cost=float(take.get("estimated_cost") or 0),
        reason=root_cause,
        warnings=warnings,
    )
    project_repository.create_retake_plan({"take_id": take_id, "verdict": plan.verdict, "result_json": plan.model_dump()})
    return plan.model_dump()

