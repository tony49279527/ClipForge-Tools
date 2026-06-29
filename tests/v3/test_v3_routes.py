from __future__ import annotations

import importlib


V3_TABLES = {
    "v3_projects",
    "v3_product_truth",
    "v3_assets",
    "v3_shots",
    "v3_prompt_versions",
    "v3_takes",
    "v3_reviews",
    "v3_continuity_states",
    "v3_usage_events",
    "schema_migrations",
}


def _create_project(client, payload: dict) -> int:
    response = client.post("/v3/projects", data=payload, follow_redirects=False)
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[-1])


def test_v3_homepage_opens(client):
    response = client.get("/v3")
    assert response.status_code == 200
    assert "ClipForge 3.0" in response.text


def test_v3_tables_are_created_on_startup(db_conn):
    rows = db_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    table_names = {row["name"] for row in rows}
    assert V3_TABLES.issubset(table_names)


def test_v3_migrations_are_idempotent(app_env, db_conn):
    migrations = importlib.import_module("clipforge_v3.migrations")
    first = migrations.run_v3_migrations()
    second = migrations.run_v3_migrations()
    assert isinstance(first, list)
    assert second == []
    applied = {row["name"] for row in db_conn.execute("SELECT name FROM schema_migrations").fetchall()}
    assert "20260616_create_v3_core_tables" in applied
    assert "20260616_expand_v3_director_tables" in applied


def test_v3_project_can_be_created(client, db_conn):
    project_id = _create_project(
        client,
        {
            "project_name": "ClipForge 3.0 Drill Director",
            "product_name": "Cordless Impact Driver",
            "product_category": "power tools",
            "target_market": "US",
            "target_audience": "Amazon power tool buyers",
            "target_platform": "amazon",
            "aspect_ratio": "16:9",
            "total_duration": "24",
            "default_clip_duration": "5",
            "resolution": "1080p",
            "language": "en",
            "source_description": "Compact cordless impact driver with battery pack, chuck, LED light, and visible fastener driving action on a garage workbench.",
        },
    )
    project = db_conn.execute(
        "SELECT id, project_name, current_stage, project_status FROM v3_projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    assert project["project_name"] == "ClipForge 3.0 Drill Director"
    assert project["current_stage"] == "product_truth"
    assert project["project_status"] == "draft"
    truth_count = db_conn.execute("SELECT COUNT(*) AS count FROM v3_product_truth WHERE project_id = ?", (project_id,)).fetchone()["count"]
    shot_count = db_conn.execute("SELECT COUNT(*) AS count FROM v3_shots WHERE project_id = ?", (project_id,)).fetchone()["count"]
    assert truth_count == 1
    assert shot_count == 0


def test_v3_project_status_endpoint_returns_stage_and_counts(client, buffing_wheel_payload):
    project_id = _create_project(client, {k: str(v) if isinstance(v, int) else v for k, v in buffing_wheel_payload.items()})
    response = client.get(f"/v3/projects/{project_id}/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["current_stage"] == "product_truth"
    assert payload["step_counts"]["product_truth"] == 1
    assert payload["step_counts"]["shot_contracts"] == 0


def test_v3_disable_flag_does_not_break_v1_v2(client, monkeypatch):
    monkeypatch.setenv("CLIPFORGE_V3_ENABLED", "false")
    assert client.get("/v3").status_code == 404
    assert client.get("/").status_code == 200
    assert client.get("/v2").status_code == 200


def test_v3_creation_does_not_write_v2_storyboard_frames(client, db_conn, buffing_wheel_payload):
    _create_project(client, {k: str(v) if isinstance(v, int) else v for k, v in buffing_wheel_payload.items()})
    storyboard_count = db_conn.execute("SELECT COUNT(*) AS count FROM storyboard_frames").fetchone()["count"]
    assert storyboard_count == 0


def test_product_truth_confirm_then_director_plan(client, db_conn, buffing_wheel_payload, sample_image_file):
    project_id = _create_project(client, {k: str(v) if isinstance(v, int) else v for k, v in buffing_wheel_payload.items()})
    response = client.post(
        f"/v3/projects/{project_id}/product-truth/confirm",
        follow_redirects=False,
    )
    assert response.status_code == 303

    with sample_image_file.open("rb") as handle:
        upload_response = client.post(
            f"/v3/projects/{project_id}/assets",
            data={
                "primary_role": "product_identity",
                "secondary_role": "",
                "must_transfer": "overall geometry,center hole,concentric stitched rings,natural off-white color",
                "must_not_transfer": "background,lighting,camera angle",
                "applies_to_shots": "S01,S03",
                "is_identity_anchor": "true",
                "user_approved": "true",
            },
            files={"asset_file": ("identity.png", handle, "image/png")},
            follow_redirects=False,
        )
    assert upload_response.status_code == 303

    plan_response = client.post(f"/v3/projects/{project_id}/director-plan/generate", follow_redirects=False)
    assert plan_response.status_code == 303

    shots = db_conn.execute(
        "SELECT shot_id, purpose, mode, primary_spend, status FROM v3_shots WHERE project_id = ? ORDER BY sequence_index",
        (project_id,),
    ).fetchall()
    assert len(shots) >= 4
    assert shots[0]["purpose"] == "product_structure_proof"
    assert any(row["purpose"] == "installation_relationship_proof" for row in shots)
    assert any(row["purpose"] == "working_surface_proof" for row in shots)


def test_product_truth_update_invalidates_unlocked_shots(client, db_conn, buffing_wheel_payload, sample_image_file):
    project_id = _create_project(client, {k: str(v) if isinstance(v, int) else v for k, v in buffing_wheel_payload.items()})
    client.post(f"/v3/projects/{project_id}/product-truth/confirm")
    with sample_image_file.open("rb") as handle:
        client.post(
            f"/v3/projects/{project_id}/assets",
            data={"primary_role": "product_identity"},
            files={"asset_file": ("identity.png", handle, "image/png")},
        )
    client.post(f"/v3/projects/{project_id}/director-plan/generate")
    client.post(
        f"/v3/projects/{project_id}/product-truth/save",
        data={
            "source_description": buffing_wheel_payload["source_description"] + " Approximate ply count should remain uncertain.",
            "product_url": buffing_wheel_payload["product_url"],
            "dimensions_input": buffing_wheel_payload["dimensions_input"],
            "materials_input": buffing_wheel_payload["materials_input"],
            "package_quantity": buffing_wheel_payload["package_quantity"],
            "parts_summary": buffing_wheel_payload["parts_summary"],
            "installation_method": buffing_wheel_payload["installation_method"],
            "working_surface_input": buffing_wheel_payload["working_surface_input"],
            "intended_for": buffing_wheel_payload["intended_for"],
            "not_for": buffing_wheel_payload["not_for"],
            "safety_notes": buffing_wheel_payload["safety_notes"],
        },
    )
    invalidated = db_conn.execute(
        "SELECT COUNT(*) AS count FROM v3_shots WHERE project_id = ? AND status = 'invalidated'",
        (project_id,),
    ).fetchone()["count"]
    assert invalidated >= 1
