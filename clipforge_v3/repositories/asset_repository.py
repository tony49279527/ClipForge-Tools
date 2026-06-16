from __future__ import annotations

import json
from typing import Any

from db import get_conn, utc_now

from clipforge_v3.migrations import ensure_v3_schema


def create_asset(payload: dict[str, Any]) -> int:
    ensure_v3_schema()
    now = utc_now()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO v3_assets (
            project_id, asset_type, original_filename, local_path, remote_url, mime_type,
            primary_role, secondary_role, must_transfer_json, must_not_transfer_json,
            applies_to_shots_json, is_identity_anchor, user_approved, metadata_json,
            audit_report_json, created_at, updated_at, storage_backend, access_url, replaced_by_asset_id, deleted_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["project_id"],
            payload["asset_type"],
            payload["original_filename"],
            payload.get("local_path"),
            payload.get("remote_url"),
            payload.get("mime_type"),
            payload["primary_role"],
            payload.get("secondary_role"),
            json.dumps(payload.get("must_transfer_json", []), ensure_ascii=False),
            json.dumps(payload.get("must_not_transfer_json", []), ensure_ascii=False),
            json.dumps(payload.get("applies_to_shots_json", []), ensure_ascii=False),
            1 if payload.get("is_identity_anchor") else 0,
            1 if payload.get("user_approved") else 0,
            json.dumps(payload.get("metadata_json", {}), ensure_ascii=False),
            json.dumps(payload.get("audit_report_json", {}), ensure_ascii=False),
            now,
            now,
            payload.get("storage_backend", "local"),
            payload.get("access_url"),
            payload.get("replaced_by_asset_id"),
            payload.get("deleted_at"),
        ),
    )
    row_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return row_id


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
    keys = list(payload.keys())
    values = [payload[key] for key in keys] + [asset_id]
    assignments = ", ".join(f"{key} = ?" for key in keys)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"UPDATE v3_assets SET {assignments} WHERE id = ?", values)
    conn.commit()
    conn.close()


def get_asset(asset_id: int):
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM v3_assets WHERE id = ?", (asset_id,))
    row = cur.fetchone()
    conn.close()
    return row


def list_assets(project_id: int) -> list:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM v3_assets WHERE project_id = ? ORDER BY id ASC", (project_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def soft_delete_asset(asset_id: int) -> None:
    update_asset(asset_id, {"deleted_at": utc_now(), "user_approved": 0})


def replace_asset(old_asset_id: int, new_asset_id: int) -> None:
    update_asset(old_asset_id, {"replaced_by_asset_id": new_asset_id, "deleted_at": utc_now(), "user_approved": 0})
