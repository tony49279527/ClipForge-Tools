from __future__ import annotations

import json
import os
from pathlib import Path

from db import utc_now

from clipforge_v3.repositories import project_repository, shot_repository, take_repository


def _decode_take(row: dict) -> dict:
    payload = dict(row)
    for field in ("generation_settings_json", "source_asset_ids_json", "qc_frame_paths_json", "review_summary_json"):
        default = "{}" if field in {"generation_settings_json", "review_summary_json"} else "[]"
        payload[field] = json.loads(payload[field] or default)
    payload["selected_by_user"] = bool(payload.get("selected_by_user"))
    payload["uncontrolled_revision"] = bool(payload.get("uncontrolled_revision"))
    payload["deleted_local_file"] = bool(payload.get("deleted_local_file"))
    return payload


def list_takes(project_id: int) -> list[dict]:
    return [_decode_take(dict(row)) for row in take_repository.list_takes(project_id)]


def select_take(project_id: int, take_id: int) -> None:
    take = _decode_take(dict(take_repository.get_take(take_id)))
    take_repository.clear_selected_for_shot(take["shot_id"])
    take_repository.update_take(take_id, {"selected_by_user": 1, "selected_at": utc_now()})
    shot_repository.update_shot(take["shot_id"], {"selected_take_id": take_id, "status": "selected"})
    project_repository.invalidate_final_assemblies(project_id)
    project_repository.update_project(project_id, {"final_assembly_valid": 0})


def clear_selected_take(project_id: int, shot_db_id: int) -> None:
    take_repository.clear_selected_for_shot(shot_db_id)
    shot_repository.update_shot(shot_db_id, {"selected_take_id": None, "status": "approved"})
    project_repository.invalidate_final_assemblies(project_id)
    project_repository.update_project(project_id, {"final_assembly_valid": 0})


def compare_takes(left_take_id: int, right_take_id: int) -> dict:
    left = _decode_take(dict(take_repository.get_take(left_take_id)))
    right = _decode_take(dict(take_repository.get_take(right_take_id)))
    diff = {}
    for field in ("prompt_version_id", "seed", "changed_variable", "previous_value", "new_value", "status", "estimated_cost"):
        if left.get(field) != right.get(field):
            diff[field] = {"left": left.get(field), "right": right.get(field)}
    return diff


def mark_local_file_deleted(take_id: int) -> None:
    take = _decode_take(dict(take_repository.get_take(take_id)))
    local_path = take.get("local_path")
    if local_path and Path(local_path).exists():
        Path(local_path).unlink()
    take_repository.update_take(take_id, {"deleted_local_file": 1})


def restore_take_history(project_id: int, take_id: int) -> None:
    take = _decode_take(dict(take_repository.get_take(take_id)))
    if not take.get("deleted_local_file"):
        select_take(project_id, take_id)
        return
    take_repository.update_take(take_id, {"deleted_local_file": 0})
    select_take(project_id, take_id)

