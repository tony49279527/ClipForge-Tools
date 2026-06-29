from __future__ import annotations

import json
from typing import Any

from db import get_conn, insert_row, select_all, select_one, update_row_by_id, utc_now

from clipforge_v3.migrations import ensure_v3_schema


def create_asset(payload: dict[str, Any]) -> int:
    ensure_v3_schema()
    now = utc_now()
    return insert_row(
        "v3_assets",
        {
            "project_id": payload["project_id"],
            "asset_type": payload["asset_type"],
            "original_filename": payload["original_filename"],
            "local_path": payload.get("local_path"),
            "remote_url": payload.get("remote_url"),
            "mime_type": payload.get("mime_type"),
            "primary_role": payload["primary_role"],
            "secondary_role": payload.get("secondary_role"),
            "must_transfer_json": json.dumps(payload.get("must_transfer_json", []), ensure_ascii=False),
            "must_not_transfer_json": json.dumps(payload.get("must_not_transfer_json", []), ensure_ascii=False),
            "applies_to_shots_json": json.dumps(payload.get("applies_to_shots_json", []), ensure_ascii=False),
            "is_identity_anchor": 1 if payload.get("is_identity_anchor") else 0,
            "user_approved": 1 if payload.get("user_approved") else 0,
            "metadata_json": json.dumps(payload.get("metadata_json", {}), ensure_ascii=False),
            "audit_report_json": json.dumps(payload.get("audit_report_json", {}), ensure_ascii=False),
            "created_at": now,
            "updated_at": now,
            "storage_backend": payload.get("storage_backend", "local"),
            "access_url": payload.get("access_url"),
            "object_key": payload.get("object_key"),
            "content_type": payload.get("content_type") or payload.get("mime_type"),
            "size_bytes": payload.get("size_bytes"),
            "replaced_by_asset_id": payload.get("replaced_by_asset_id"),
            "deleted_at": payload.get("deleted_at"),
        },
    )


def update_asset(asset_id: int, fields: dict[str, Any]) -> None:
    if not fields:
        return
    ensure_v3_schema()
    payload = dict(fields)
    json_fields = {
        "must_transfer_json",
        "must_not_transfer_json",
        "applies_to_shots_json",
        "metadata_json",
        "audit_report_json",
    }
    for key in json_fields.intersection(payload):
        payload[key] = json.dumps(payload[key], ensure_ascii=False)
    payload["updated_at"] = utc_now()
    update_row_by_id("v3_assets", asset_id, payload)


def get_asset(asset_id: int):
    ensure_v3_schema()
    return select_one("SELECT * FROM v3_assets WHERE id = :asset_id", {"asset_id": asset_id})


def list_assets(project_id: int) -> list:
    ensure_v3_schema()
    return select_all("SELECT * FROM v3_assets WHERE project_id = :project_id ORDER BY id ASC", {"project_id": project_id})


def soft_delete_asset(asset_id: int) -> None:
    update_asset(asset_id, {"deleted_at": utc_now(), "user_approved": 0})


def replace_asset(old_asset_id: int, new_asset_id: int) -> None:
    update_asset(old_asset_id, {"replaced_by_asset_id": new_asset_id, "deleted_at": utc_now(), "user_approved": 0})
