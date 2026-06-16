from __future__ import annotations

import json
import os

from db import get_conn

from clipforge_v3.repositories import project_repository, shot_repository, take_repository


V3_IDENTITY_REANCHOR_INTERVAL = int(os.getenv("V3_IDENTITY_REANCHOR_INTERVAL", "2"))


def bootstrap_continuity_states(*, project_id: int, shots: list[dict]) -> None:
    for shot in shots:
        project_repository.create_continuity_state(
            {
                "project_id": project_id,
                "shot_id": shot["id"],
                "product_state_json": shot["start_state_json"],
                "machine_state_json": {"mode": shot["mode"]},
                "workpiece_state_json": {"status": "pending_action"},
                "camera_state_json": shot["camera_contract_json"],
                "lighting_state_json": shot["lighting_contract_json"],
                "environment_state_json": {"continuity_anchor": shot["continuity_anchors_json"]},
                "action_state_json": {"subject_action": shot["subject_action"]},
                "sound_state_json": shot["audio_contract_json"],
                "source_take_id": None,
                "version": 1,
            }
        )


def list_continuity_states(project_id: int) -> list[dict]:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM v3_continuity_states WHERE project_id = ? ORDER BY shot_id ASC, version DESC, id DESC", (project_id,))
    rows = []
    for row in cur.fetchall():
        payload = dict(row)
        for field in (
            "product_state_json",
            "machine_state_json",
            "workpiece_state_json",
            "camera_state_json",
            "lighting_state_json",
            "environment_state_json",
            "action_state_json",
            "sound_state_json",
        ):
            payload[field] = json.loads(payload[field] or "{}")
        rows.append(payload)
    conn.close()
    return rows


def get_latest_continuity_state_for_shot(shot_db_id: int) -> dict | None:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM v3_continuity_states WHERE shot_id = ? ORDER BY version DESC, id DESC LIMIT 1", (shot_db_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    payload = dict(row)
    for field in (
        "product_state_json",
        "machine_state_json",
        "workpiece_state_json",
        "camera_state_json",
        "lighting_state_json",
        "environment_state_json",
        "action_state_json",
        "sound_state_json",
    ):
        payload[field] = json.loads(payload[field] or "{}")
    return payload


def build_continuity_context(project_id: int, shot: dict) -> dict:
    prior_shot = None
    if shot.get("depends_on_shot_id"):
        prior_shot = shot_repository.get_shot_by_business_id(project_id, shot["depends_on_shot_id"])
    if not prior_shot and shot.get("sequence_index", 1) > 1:
        for candidate in shot_repository.list_shots(project_id):
            if candidate["sequence_index"] == shot["sequence_index"] - 1:
                prior_shot = candidate
                break
    latest_state = get_latest_continuity_state_for_shot(prior_shot["id"]) if prior_shot else None
    same_group_chain_index = 0
    if shot.get("continuity_group"):
        for candidate in shot_repository.list_shots(project_id):
            if candidate["sequence_index"] <= shot["sequence_index"] and candidate["continuity_group"] == shot["continuity_group"]:
                same_group_chain_index += 1
    return {
        "ledger": latest_state or {},
        "reanchor_identity": same_group_chain_index > 1 and (same_group_chain_index - 1) % max(V3_IDENTITY_REANCHOR_INTERVAL, 1) == 0,
        "same_group_chain_index": same_group_chain_index,
    }


def record_continuity_from_take(*, project_id: int, shot: dict, take_id: int, take_row: dict) -> int:
    previous = get_latest_continuity_state_for_shot(shot["id"])
    version = int(previous["version"]) + 1 if previous else 1
    payload = {
        "project_id": project_id,
        "shot_id": shot["id"],
        "product_state_json": shot["end_state_json"] or shot["start_state_json"],
        "machine_state_json": {
            "power": shot["end_state_json"].get("power", "off"),
            "spindle_direction": shot["end_state_json"].get("spindle_direction", "stopped"),
            "installed_components": shot["end_state_json"].get("installed_components", []),
        },
        "workpiece_state_json": shot["end_state_json"].get("workpiece_state", {}),
        "camera_state_json": shot["camera_contract_json"] | {"movement_endpoint": shot["camera_contract_json"].get("endpoint", "")},
        "lighting_state_json": shot["lighting_contract_json"],
        "environment_state_json": shot["continuity_anchors_json"],
        "action_state_json": {"subject_action": shot["subject_action"], "take_id": take_id},
        "sound_state_json": shot["audio_contract_json"],
        "source_take_id": take_id,
        "version": version,
    }
    return project_repository.create_continuity_state(payload)

