from __future__ import annotations

import json
import sqlite3
from typing import Any

from db import get_conn, utc_now

from clipforge_v3.migrations import ensure_v3_schema


def create_project(payload: dict[str, Any]) -> int:
    ensure_v3_schema()
    now = utc_now()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO v3_projects (
            project_name, product_name, product_category, target_market, target_audience,
            target_platform, aspect_ratio, total_duration, default_clip_duration,
            resolution, language, project_status, current_stage, product_url,
            dimensions_input, materials_input, package_quantity, parts_summary,
            installation_method, working_surface_input, intended_for, not_for,
            safety_notes, director_plan_status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["project_name"],
            payload["product_name"],
            payload["product_category"],
            payload["target_market"],
            payload["target_audience"],
            payload["target_platform"],
            payload["aspect_ratio"],
            payload["total_duration"],
            payload["default_clip_duration"],
            payload["resolution"],
            payload["language"],
            payload.get("project_status", "draft"),
            payload.get("current_stage", "project_brief"),
            payload.get("product_url", ""),
            payload.get("dimensions_input", ""),
            payload.get("materials_input", ""),
            payload.get("package_quantity", ""),
            payload.get("parts_summary", ""),
            payload.get("installation_method", ""),
            payload.get("working_surface_input", ""),
            payload.get("intended_for", ""),
            payload.get("not_for", ""),
            payload.get("safety_notes", ""),
            payload.get("director_plan_status", "not_started"),
            now,
            now,
        ),
    )
    project_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return project_id


def list_projects() -> list:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM v3_projects ORDER BY id DESC")
    rows = cur.fetchall()
    conn.close()
    return rows


def get_project(project_id: int):
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM v3_projects WHERE id = ?", (project_id,))
    row = cur.fetchone()
    conn.close()
    return row


def update_project(project_id: int, fields: dict[str, Any]) -> None:
    if not fields:
        return
    ensure_v3_schema()
    payload = dict(fields)
    payload["updated_at"] = utc_now()
    keys = list(payload.keys())
    values = [payload[key] for key in keys] + [project_id]
    assignments = ", ".join(f"{key} = ?" for key in keys)
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(f"UPDATE v3_projects SET {assignments} WHERE id = ?", values)
    conn.commit()
    conn.close()


def create_product_truth(payload: dict[str, Any]) -> int:
    ensure_v3_schema()
    now = utc_now()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO v3_product_truth (
            project_id, source_description, immutable_geometry_json, dimensions_json,
            material_json, colors_json, components_json, installation_rules_json,
            working_surface_json, allowed_behaviors_json, forbidden_transformations_json,
            forbidden_materials_json, safety_constraints_json, confidence_json,
            user_approved, version, created_at, updated_at, product_truth_json, invalidates_shots
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["project_id"],
            payload["source_description"],
            json.dumps(payload.get("immutable_geometry_json", {}), ensure_ascii=False),
            json.dumps(payload.get("dimensions_json", {}), ensure_ascii=False),
            json.dumps(payload.get("material_json", {}), ensure_ascii=False),
            json.dumps(payload.get("colors_json", {}), ensure_ascii=False),
            json.dumps(payload.get("components_json", []), ensure_ascii=False),
            json.dumps(payload.get("installation_rules_json", []), ensure_ascii=False),
            json.dumps(payload.get("working_surface_json", {}), ensure_ascii=False),
            json.dumps(payload.get("allowed_behaviors_json", []), ensure_ascii=False),
            json.dumps(payload.get("forbidden_transformations_json", []), ensure_ascii=False),
            json.dumps(payload.get("forbidden_materials_json", []), ensure_ascii=False),
            json.dumps(payload.get("safety_constraints_json", []), ensure_ascii=False),
            json.dumps(payload.get("confidence_json", {}), ensure_ascii=False),
            1 if payload.get("user_approved") else 0,
            payload.get("version", 1),
            now,
            now,
            json.dumps(payload.get("product_truth_json", {}), ensure_ascii=False),
            1 if payload.get("invalidates_shots") else 0,
        ),
    )
    row_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return row_id


def list_product_truth_versions(project_id: int) -> list:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM v3_product_truth WHERE project_id = ? ORDER BY version DESC, id DESC",
        (project_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def get_latest_product_truth(project_id: int):
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM v3_product_truth WHERE project_id = ? ORDER BY version DESC, id DESC LIMIT 1",
        (project_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def get_next_product_truth_version(project_id: int) -> int:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(version), 0) AS version FROM v3_product_truth WHERE project_id = ?", (project_id,))
    row = cur.fetchone()
    conn.close()
    return int(row["version"]) + 1


def create_continuity_state(payload: dict[str, Any]) -> int:
    ensure_v3_schema()
    now = utc_now()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO v3_continuity_states (
            project_id, shot_id, product_state_json, machine_state_json, workpiece_state_json,
            camera_state_json, lighting_state_json, environment_state_json, action_state_json,
            sound_state_json, source_take_id, version, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["project_id"],
            payload["shot_id"],
            json.dumps(payload.get("product_state_json", {}), ensure_ascii=False),
            json.dumps(payload.get("machine_state_json", {}), ensure_ascii=False),
            json.dumps(payload.get("workpiece_state_json", {}), ensure_ascii=False),
            json.dumps(payload.get("camera_state_json", {}), ensure_ascii=False),
            json.dumps(payload.get("lighting_state_json", {}), ensure_ascii=False),
            json.dumps(payload.get("environment_state_json", {}), ensure_ascii=False),
            json.dumps(payload.get("action_state_json", {}), ensure_ascii=False),
            json.dumps(payload.get("sound_state_json", {}), ensure_ascii=False),
            payload.get("source_take_id"),
            payload.get("version", 1),
            now,
            now,
        ),
    )
    row_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return row_id


def create_usage_event(payload: dict[str, Any]) -> int:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    event_key = payload.get("event_key")
    try:
        cur.execute(
            """
            INSERT INTO v3_usage_events (
                project_id, shot_id, take_id, stage, provider, model, duration, resolution,
                input_tokens, output_tokens, total_tokens, estimated_cost, status, raw_usage_json,
                event_key, source_type, source_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["project_id"],
                payload.get("shot_id"),
                payload.get("take_id"),
                payload["stage"],
                payload["provider"],
                payload["model"],
                payload.get("duration"),
                payload.get("resolution"),
                payload.get("input_tokens", 0),
                payload.get("output_tokens", 0),
                payload.get("total_tokens", 0),
                payload.get("estimated_cost", 0),
                payload.get("status", "succeeded"),
                json.dumps(payload.get("raw_usage_json", {}), ensure_ascii=False),
                event_key,
                payload.get("source_type"),
                payload.get("source_id"),
                utc_now(),
            ),
        )
        event_id = int(cur.lastrowid)
        conn.commit()
    except sqlite3.IntegrityError:
        if not event_key:
            conn.close()
            raise
        conn.rollback()
        cur.execute("SELECT id FROM v3_usage_events WHERE event_key = ?", (event_key,))
        row = cur.fetchone()
        if not row:
            conn.close()
            raise
        event_id = int(row["id"])
    conn.close()
    return event_id


def create_preflight_check(payload: dict[str, Any]) -> int:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO v3_preflight_checks (
            project_id, shot_id, prompt_version_id, tier, allow_submit, result_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["project_id"],
            payload["shot_id"],
            payload.get("prompt_version_id"),
            payload["tier"],
            1 if payload.get("allow_submit") else 0,
            json.dumps(payload.get("result_json", {}), ensure_ascii=False),
            utc_now(),
        ),
    )
    row_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return row_id


def list_preflight_checks(project_id: int) -> list:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM v3_preflight_checks WHERE project_id = ? ORDER BY id DESC", (project_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def list_usage_events(project_id: int) -> list:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM v3_usage_events WHERE project_id = ? ORDER BY id DESC", (project_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def create_review(payload: dict[str, Any]) -> int:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO v3_reviews (
            take_id, verdict, product_identity_score, mechanical_accuracy_score, motion_realism_score,
            camera_execution_score, continuity_score, commercial_usability_score, error_codes_json,
            reviewer_notes, next_action, created_at, material_accuracy_score, safety, ai_suggestion_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["take_id"],
            payload["verdict"],
            payload["product_identity_score"],
            payload["mechanical_accuracy_score"],
            payload["motion_realism_score"],
            payload["camera_execution_score"],
            payload["continuity_score"],
            payload["commercial_usability_score"],
            json.dumps(payload.get("error_codes_json", []), ensure_ascii=False),
            payload.get("reviewer_notes"),
            payload["next_action"],
            utc_now(),
            payload.get("material_accuracy_score", 0),
            payload.get("safety", "pass"),
            json.dumps(payload.get("ai_suggestion_json", {}), ensure_ascii=False),
        ),
    )
    review_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return review_id


def create_retake_plan(payload: dict[str, Any]) -> int:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO v3_retake_plans (take_id, verdict, result_json, created_at) VALUES (?, ?, ?, ?)",
        (payload["take_id"], payload["verdict"], json.dumps(payload["result_json"], ensure_ascii=False), utc_now()),
    )
    row_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return row_id


def list_retake_plans(project_id: int) -> list:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT rp.*, s.shot_id AS business_shot_id
        FROM v3_retake_plans rp
        JOIN v3_takes t ON t.id = rp.take_id
        JOIN v3_shots s ON s.id = t.shot_id
        WHERE s.project_id = ?
        ORDER BY rp.id DESC
        """,
        (project_id,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


def create_final_assembly(payload: dict[str, Any]) -> int:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO v3_final_assemblies (
            project_id, version, status, output_path, assembly_take_ids_json, invalidated, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["project_id"],
            payload["version"],
            payload["status"],
            payload.get("output_path"),
            json.dumps(payload.get("assembly_take_ids_json", []), ensure_ascii=False),
            1 if payload.get("invalidated") else 0,
            utc_now(),
        ),
    )
    row_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return row_id


def list_final_assemblies(project_id: int) -> list:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM v3_final_assemblies WHERE project_id = ? ORDER BY version DESC, id DESC", (project_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_next_final_assembly_version(project_id: int) -> int:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(version), 0) AS version FROM v3_final_assemblies WHERE project_id = ?", (project_id,))
    row = cur.fetchone()
    conn.close()
    return int(row["version"]) + 1


def invalidate_final_assemblies(project_id: int) -> None:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE v3_final_assemblies SET invalidated = 1 WHERE project_id = ?", (project_id,))
    conn.commit()
    conn.close()


def create_operation_event(payload: dict[str, Any]) -> int:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO v3_operation_events (
            request_id, project_id, shot_id, take_id, task_id, provider, stage, duration,
            status, error_code, retry_count, elapsed_time, message, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["request_id"],
            payload.get("project_id"),
            payload.get("shot_id"),
            payload.get("take_id"),
            payload.get("task_id"),
            payload.get("provider"),
            payload["stage"],
            payload.get("duration", 0),
            payload["status"],
            payload.get("error_code"),
            payload.get("retry_count", 0),
            payload.get("elapsed_time", 0),
            payload.get("message", ""),
            utc_now(),
        ),
    )
    event_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return event_id


def list_operation_events(project_id: int | None = None, limit: int = 20) -> list:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    if project_id is None:
        cur.execute("SELECT * FROM v3_operation_events ORDER BY id DESC LIMIT ?", (limit,))
    else:
        cur.execute("SELECT * FROM v3_operation_events WHERE project_id = ? ORDER BY id DESC LIMIT ?", (project_id, limit))
    rows = cur.fetchall()
    conn.close()
    return rows


def get_project_metrics(project_id: int) -> dict[str, int]:
    ensure_v3_schema()
    conn = get_conn()
    cur = conn.cursor()
    metrics = {
        "project_brief": 1,
        "product_truth": 0,
        "reference_assets": 0,
        "director_plan": 0,
        "shot_contracts": 0,
        "prompt_compilation": 0,
        "generation": 0,
        "review": 0,
        "delivery": 0,
        "assets": 0,
        "shots": 0,
        "publish": 0,
        "prompt_versions": 0,
        "takes": 0,
        "continuity_states": 0,
    }
    queries = {
        "product_truth": "SELECT COUNT(*) AS count FROM v3_product_truth WHERE project_id = ?",
        "assets": "SELECT COUNT(*) AS count FROM v3_assets WHERE project_id = ?",
        "shots": "SELECT COUNT(*) AS count FROM v3_shots WHERE project_id = ?",
        "prompt_versions": """
            SELECT COUNT(*) AS count
            FROM v3_prompt_versions pv
            JOIN v3_shots s ON s.id = pv.shot_id
            WHERE s.project_id = ?
        """,
        "takes": """
            SELECT COUNT(*) AS count
            FROM v3_takes t
            JOIN v3_shots s ON s.id = t.shot_id
            WHERE s.project_id = ?
        """,
        "review": """
            SELECT COUNT(*) AS count
            FROM v3_reviews r
            JOIN v3_takes t ON t.id = r.take_id
            JOIN v3_shots s ON s.id = t.shot_id
            WHERE s.project_id = ?
        """,
        "continuity_states": "SELECT COUNT(*) AS count FROM v3_continuity_states WHERE project_id = ?",
    }
    for key, sql in queries.items():
        cur.execute(sql, (project_id,))
        metrics[key] = int(cur.fetchone()["count"])
    metrics["reference_assets"] = metrics["assets"]
    metrics["director_plan"] = 1 if metrics["shots"] else 0
    metrics["shot_contracts"] = metrics["shots"]
    metrics["prompt_compilation"] = metrics["prompt_versions"]
    metrics["generation"] = metrics["prompt_versions"] + metrics["takes"]
    conn.close()
    return metrics
