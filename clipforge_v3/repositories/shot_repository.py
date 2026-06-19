from __future__ import annotations

import json
from typing import Any

from db import get_conn, insert_row, select_all, select_one, update_row_by_id, utc_now

from clipforge_v3.migrations import ensure_v3_schema


JSON_FIELDS = {
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
}


def create_shot(payload: dict[str, Any]) -> int:
    ensure_v3_schema()
    now = utc_now()
    return insert_row(
        "v3_shots",
        {
            "project_id": payload["project_id"],
            "shot_id": payload["shot_id"],
            "sequence_index": payload["sequence_index"],
            "purpose": payload["purpose"],
            "mode": payload["mode"],
            "duration": payload["duration"],
            "primary_spend": payload["primary_spend"],
            "secondary_spend": payload.get("secondary_spend"),
            "economized_json": json.dumps(payload.get("economized_json", []), ensure_ascii=False),
            "subject_action": payload["subject_action"],
            "start_state_json": json.dumps(payload.get("start_state_json", {}), ensure_ascii=False),
            "end_state_json": json.dumps(payload.get("end_state_json", {}), ensure_ascii=False),
            "camera_contract_json": json.dumps(payload.get("camera_contract_json", {}), ensure_ascii=False),
            "lighting_contract_json": json.dumps(payload.get("lighting_contract_json", {}), ensure_ascii=False),
            "audio_contract_json": json.dumps(payload.get("audio_contract_json", {}), ensure_ascii=False),
            "reference_roles_json": json.dumps(payload.get("reference_roles_json", []), ensure_ascii=False),
            "continuity_anchors_json": json.dumps(payload.get("continuity_anchors_json", {}), ensure_ascii=False),
            "constraints_json": json.dumps(payload.get("constraints_json", []), ensure_ascii=False),
            "risk_codes_json": json.dumps(payload.get("risk_codes_json", []), ensure_ascii=False),
            "generation_strategy": payload["generation_strategy"],
            "depends_on_shot_id": payload.get("depends_on_shot_id"),
            "status": payload.get("status", "planned"),
            "user_approved": 1 if payload.get("user_approved") else 0,
            "version": payload.get("version", 1),
            "created_at": now,
            "updated_at": now,
            "commercial_beat": payload.get("commercial_beat", ""),
            "single_visible_beat": payload.get("single_visible_beat", ""),
            "mode_decision_json": json.dumps(payload.get("mode_decision_json", {}), ensure_ascii=False),
            "fidelity_json": json.dumps(payload.get("fidelity_json", {}), ensure_ascii=False),
            "locked_by_user": 1 if payload.get("locked_by_user") else 0,
            "continuity_group": payload.get("continuity_group", "default"),
            "selected_take_id": payload.get("selected_take_id"),
            "max_draft_takes": payload.get("max_draft_takes"),
            "max_production_takes": payload.get("max_production_takes"),
            "max_cost_cny": payload.get("max_cost_cny"),
            "max_generation_seconds": payload.get("max_generation_seconds"),
            "good_enough_definition": payload.get("good_enough_definition", ""),
        },
    )


def update_shot(shot_db_id: int, fields: dict[str, Any]) -> None:
    if not fields:
        return
    ensure_v3_schema()
    payload = dict(fields)
    for key in JSON_FIELDS.intersection(payload):
        payload[key] = json.dumps(payload[key], ensure_ascii=False)
    payload["updated_at"] = utc_now()
    update_row_by_id("v3_shots", shot_db_id, payload)


def delete_shot(shot_db_id: int) -> None:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM v3_prompt_versions WHERE shot_id = ?", (shot_db_id,))
    cur.execute("DELETE FROM v3_takes WHERE shot_id = ?", (shot_db_id,))
    cur.execute("DELETE FROM v3_continuity_states WHERE shot_id = ?", (shot_db_id,))
    cur.execute("DELETE FROM v3_shots WHERE id = ?", (shot_db_id,))
    conn.commit()
    conn.close()


def list_shots(project_id: int) -> list:
    ensure_v3_schema()
    return select_all("SELECT * FROM v3_shots WHERE project_id = :project_id ORDER BY sequence_index ASC, id ASC", {"project_id": project_id})


def get_shot(db_shot_id: int):
    ensure_v3_schema()
    return select_one("SELECT * FROM v3_shots WHERE id = :db_shot_id", {"db_shot_id": db_shot_id})


def get_shot_by_business_id(project_id: int, business_shot_id: str):
    ensure_v3_schema()
    return select_one(
        "SELECT * FROM v3_shots WHERE project_id = :project_id AND shot_id = :business_shot_id",
        {"project_id": project_id, "business_shot_id": business_shot_id},
    )


def invalidate_unlocked_shots(project_id: int) -> None:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE v3_shots
        SET status = 'invalidated', updated_at = ?
        WHERE project_id = ? AND COALESCE(locked_by_user, 0) = 0
        """,
        (utc_now(), project_id),
    )
    conn.commit()
    conn.close()


def renumber_shots(project_id: int) -> None:
    rows = list_shots(project_id)
    conn = get_conn()
    cur = conn.cursor()
    now = utc_now()
    for index, row in enumerate(rows, start=1):
        cur.execute(
            "UPDATE v3_shots SET sequence_index = ?, shot_id = ?, updated_at = ? WHERE id = ?",
            (index, f"S{index:02d}", now, row["id"]),
        )
    conn.commit()
    conn.close()


def swap_shot_order(project_id: int, shot_db_id: int, direction: str) -> None:
    rows = list_shots(project_id)
    ordered = list(rows)
    idx = next((i for i, row in enumerate(ordered) if row["id"] == shot_db_id), None)
    if idx is None:
        raise KeyError(f"Shot {shot_db_id} not found")
    if direction == "up" and idx == 0:
        return
    if direction == "down" and idx == len(ordered) - 1:
        return
    other_idx = idx - 1 if direction == "up" else idx + 1
    conn = get_conn()
    cur = conn.cursor()
    now = utc_now()
    cur.execute("UPDATE v3_shots SET sequence_index = ?, updated_at = ? WHERE id = ?", (ordered[other_idx]["sequence_index"], now, ordered[idx]["id"]))
    cur.execute("UPDATE v3_shots SET sequence_index = ?, updated_at = ? WHERE id = ?", (ordered[idx]["sequence_index"], now, ordered[other_idx]["id"]))
    conn.commit()
    conn.close()


def create_prompt_version(payload: dict[str, Any]) -> int:
    ensure_v3_schema()
    return insert_row(
        "v3_prompt_versions",
        {
            "shot_id": payload["shot_id"],
            "version": payload["version"],
            "mode": payload["mode"],
            "prompt_text": payload["prompt_text"],
            "prompt_char_count": payload["prompt_char_count"],
            "prompt_language": payload["prompt_language"],
            "role_map_json": json.dumps(payload.get("role_map_json", {}), ensure_ascii=False),
            "compiler_warnings_json": json.dumps(payload.get("compiler_warnings_json", []), ensure_ascii=False),
            "validation_result_json": json.dumps(payload.get("validation_result_json", {}), ensure_ascii=False),
            "created_at": utc_now(),
            "raw_draft_prompt": payload.get("raw_draft_prompt", ""),
            "anti_slop_prompt": payload.get("anti_slop_prompt", ""),
            "compressed_prompt": payload.get("compressed_prompt", ""),
            "removed_items_json": json.dumps(payload.get("removed_items_json", []), ensure_ascii=False),
            "provider_payload_json": json.dumps(payload.get("provider_payload_json", {}), ensure_ascii=False),
            "allow_submit": 1 if payload.get("allow_submit") else 0,
            "locked_by_user": 1 if payload.get("locked_by_user") else 0,
        },
    )


def delete_prompt_versions_for_project(project_id: int) -> None:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM v3_prompt_versions WHERE shot_id IN (SELECT id FROM v3_shots WHERE project_id = ? AND COALESCE(locked_by_user, 0) = 0)",
        (project_id,),
    )
    conn.commit()
    conn.close()


def list_prompt_versions_by_shot(shot_id: int) -> list:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM v3_prompt_versions WHERE shot_id = ? ORDER BY version DESC, id DESC", (shot_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_prompt_version(prompt_version_id: int):
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM v3_prompt_versions WHERE id = ?", (prompt_version_id,))
    row = cur.fetchone()
    conn.close()
    return row


def get_next_prompt_version(shot_id: int) -> int:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(version), 0) AS version FROM v3_prompt_versions WHERE shot_id = ?", (shot_id,))
    row = cur.fetchone()
    conn.close()
    return int(row["version"]) + 1


def update_prompt_version(prompt_version_id: int, fields: dict[str, Any]) -> None:
    if not fields:
        return
    ensure_v3_schema()
    payload = dict(fields)
    json_fields = {
        "role_map_json",
        "compiler_warnings_json",
        "validation_result_json",
        "removed_items_json",
        "provider_payload_json",
    }
    for key in json_fields.intersection(payload):
        payload[key] = json.dumps(payload[key], ensure_ascii=False)
    keys = list(payload.keys())
    values = [payload[key] for key in keys] + [prompt_version_id]
    assignments = ", ".join(f"{key} = ?" for key in keys)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"UPDATE v3_prompt_versions SET {assignments} WHERE id = ?", values)
    conn.commit()
    conn.close()


def list_prompt_versions(project_id: int) -> list:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT pv.*, s.shot_id AS business_shot_id
        FROM v3_prompt_versions pv
        JOIN v3_shots s ON s.id = pv.shot_id
        WHERE s.project_id = ?
        ORDER BY s.sequence_index ASC, pv.version DESC
        """,
        (project_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows
