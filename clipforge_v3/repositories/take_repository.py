from __future__ import annotations

import json
from typing import Any

from db import DatabaseIntegrityError, get_conn, insert_row, select_all, select_one, update_row_by_id, utc_now

from clipforge_v3.migrations import ensure_v3_schema


def create_take(payload: dict[str, Any]) -> int:
    ensure_v3_schema()
    return insert_row(
        "v3_takes",
        {
            "shot_id": payload["shot_id"],
            "take_number": payload["take_number"],
            "prompt_version_id": payload["prompt_version_id"],
            "seedance_task_id": payload.get("seedance_task_id"),
            "status": payload.get("status", "queued"),
            "local_path": payload.get("local_path"),
            "remote_url": payload.get("remote_url"),
            "first_frame_path": payload.get("first_frame_path"),
            "last_frame_path": payload.get("last_frame_path"),
            "seed": payload.get("seed"),
            "generation_settings_json": json.dumps(payload.get("generation_settings_json", {}), ensure_ascii=False),
            "changed_variable": payload.get("changed_variable"),
            "parent_take_id": payload.get("parent_take_id"),
            "token_usage": payload.get("token_usage", 0),
            "estimated_cost": payload.get("estimated_cost", 0),
            "created_at": utc_now(),
            "tier": payload.get("tier", "draft"),
            "previous_value": payload.get("previous_value"),
            "new_value": payload.get("new_value"),
            "change_reason": payload.get("change_reason", ""),
            "source_asset_ids_json": json.dumps(payload.get("source_asset_ids_json", []), ensure_ascii=False),
            "qc_frame_paths_json": json.dumps(payload.get("qc_frame_paths_json", []), ensure_ascii=False),
            "selected_by_user": 1 if payload.get("selected_by_user") else 0,
            "selected_at": payload.get("selected_at"),
            "uncontrolled_revision": 1 if payload.get("uncontrolled_revision") else 0,
            "deleted_local_file": 1 if payload.get("deleted_local_file") else 0,
            "restored_from_take_id": payload.get("restored_from_take_id"),
            "review_summary_json": json.dumps(payload.get("review_summary_json", {}), ensure_ascii=False),
            "idempotency_key": payload.get("idempotency_key"),
            "submission_status": payload.get("submission_status"),
            "provider_task_id": payload.get("provider_task_id"),
            "provider_request_hash": payload.get("provider_request_hash"),
            "submission_started_at": payload.get("submission_started_at"),
            "submission_completed_at": payload.get("submission_completed_at"),
            "retry_count": payload.get("retry_count", 0),
            "last_poll_at": payload.get("last_poll_at"),
            "generation_submission_id": payload.get("generation_submission_id"),
            "storage_backend": payload.get("storage_backend", "local"),
            "object_key": payload.get("object_key"),
            "content_type": payload.get("content_type"),
            "size_bytes": payload.get("size_bytes"),
        },
    )


def list_takes(project_id: int) -> list:
    ensure_v3_schema()
    return select_all(
        """
        SELECT t.*, s.shot_id AS business_shot_id, s.sequence_index
        FROM v3_takes t
        JOIN v3_shots s ON s.id = t.shot_id
        WHERE s.project_id = :project_id
        ORDER BY s.sequence_index ASC, t.take_number ASC
        """,
        {"project_id": project_id},
    )


def list_takes_for_shot(shot_id: int) -> list:
    ensure_v3_schema()
    return select_all("SELECT * FROM v3_takes WHERE shot_id = :shot_id ORDER BY take_number ASC, id ASC", {"shot_id": shot_id})


def get_take(take_id: int):
    ensure_v3_schema()
    return select_one("SELECT * FROM v3_takes WHERE id = :take_id", {"take_id": take_id})


def get_take_by_generation_submission_id(submission_id: int):
    ensure_v3_schema()
    return select_one(
        "SELECT * FROM v3_takes WHERE generation_submission_id = :submission_id",
        {"submission_id": submission_id},
    )


def get_or_create_take_for_submission(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    submission_id = payload.get("generation_submission_id")
    if not submission_id:
        take_id = create_take(payload)
        row = get_take(take_id)
        return dict(row), True
    existing = get_take_by_generation_submission_id(int(submission_id))
    if existing:
        return dict(existing), False
    try:
        take_id = create_take(payload)
    except DatabaseIntegrityError:
        existing = get_take_by_generation_submission_id(int(submission_id))
        if existing:
            return dict(existing), False
        raise
    row = get_take(take_id)
    return dict(row), True


def update_take(take_id: int, fields: dict[str, Any]) -> None:
    if not fields:
        return
    ensure_v3_schema()
    payload = dict(fields)
    json_fields = {"generation_settings_json", "source_asset_ids_json", "qc_frame_paths_json", "review_summary_json"}
    for key in json_fields.intersection(payload):
        payload[key] = json.dumps(payload[key], ensure_ascii=False)
    update_row_by_id("v3_takes", take_id, payload)


def reserve_generation_submission(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    ensure_v3_schema()
    now = utc_now()
    conn = get_conn()
    cur = conn.cursor()
    try:
        insert_row(
            "v3_generation_submissions",
            {
                "project_id": payload["project_id"],
                "shot_id": payload["shot_id"],
                "prompt_version_id": payload["prompt_version_id"],
                "generation_tier": payload["generation_tier"],
                "provider": payload["provider"],
                "idempotency_key": payload["idempotency_key"],
                "provider_request_hash": payload["provider_request_hash"],
                "submission_status": payload.get("submission_status", "reserved"),
                "paid_confirmed": 1 if payload.get("paid_confirmed") else 0,
                "confirmation_token": payload.get("confirmation_token"),
                "request_payload_json": json.dumps(payload.get("request_payload_json", {}), ensure_ascii=False),
                "response_json": json.dumps(payload.get("response_json", {}), ensure_ascii=False),
                "error_json": json.dumps(payload.get("error_json", {}), ensure_ascii=False),
                "retry_count": payload.get("retry_count", 0),
                "budget_approved_at": payload.get("budget_approved_at"),
                "created_at": now,
                "updated_at": now,
            },
        )
        created = True
    except DatabaseIntegrityError:
        created = False
    row = dict(select_one(
        "SELECT * FROM v3_generation_submissions WHERE idempotency_key = :idempotency_key",
        {"idempotency_key": payload["idempotency_key"]},
    ))
    return _decode_submission(row), created


def get_generation_submission(submission_id: int) -> dict[str, Any] | None:
    ensure_v3_schema()
    row = select_one("SELECT * FROM v3_generation_submissions WHERE id = :submission_id", {"submission_id": submission_id})
    return _decode_submission(dict(row)) if row else None


def get_generation_submission_by_key(idempotency_key: str) -> dict[str, Any] | None:
    ensure_v3_schema()
    row = select_one(
        "SELECT * FROM v3_generation_submissions WHERE idempotency_key = :idempotency_key",
        {"idempotency_key": idempotency_key},
    )
    return _decode_submission(dict(row)) if row else None


def list_generation_submissions(project_id: int) -> list[dict[str, Any]]:
    ensure_v3_schema()
    rows = [
        _decode_submission(dict(row))
        for row in select_all(
            "SELECT * FROM v3_generation_submissions WHERE project_id = :project_id ORDER BY id DESC",
            {"project_id": project_id},
        )
    ]
    return rows


def update_generation_submission(submission_id: int, fields: dict[str, Any]) -> None:
    if not fields:
        return
    ensure_v3_schema()
    payload = dict(fields)
    json_fields = {"request_payload_json", "response_json", "error_json"}
    for key in json_fields.intersection(payload):
        payload[key] = json.dumps(payload[key], ensure_ascii=False)
    payload["updated_at"] = utc_now()
    update_row_by_id("v3_generation_submissions", submission_id, payload)


def claim_generation_submission_for_submit(submission_id: int) -> bool:
    ensure_v3_schema()
    now = utc_now()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE v3_generation_submissions
        SET submission_status = 'submitting',
            submission_started_at = COALESCE(submission_started_at, :now),
            updated_at = :now
        WHERE id = :submission_id
          AND provider_task_id IS NULL
          AND budget_approved_at IS NOT NULL
          AND submission_status IN ('reserved', 'queued')
        """,
        {"now": now, "submission_id": submission_id},
    )
    claimed = cur.rowcount == 1
    conn.commit()
    conn.close()
    return claimed


def _decode_submission(row: dict[str, Any]) -> dict[str, Any]:
    for field in ("request_payload_json", "response_json", "error_json"):
        row[field] = json.loads(row.get(field) or "{}")
    row["paid_confirmed"] = bool(row.get("paid_confirmed"))
    return row


def clear_selected_for_shot(shot_id: int) -> None:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE v3_takes SET selected_by_user = 0, selected_at = NULL WHERE shot_id = ?", (shot_id,))
    conn.commit()
    conn.close()


def get_next_take_number(shot_id: int) -> int:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(take_number), 0) AS take_number FROM v3_takes WHERE shot_id = ?", (shot_id,))
    row = cur.fetchone()
    conn.close()
    return int(row["take_number"]) + 1
