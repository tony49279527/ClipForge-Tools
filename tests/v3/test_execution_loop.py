from __future__ import annotations

import importlib
from pathlib import Path


def _create_project(client, payload: dict) -> int:
    response = client.post("/v3/projects", data=payload, follow_redirects=False)
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[-1])


def _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file) -> tuple[int, list[dict]]:
    project_id = _create_project(client, {k: str(v) if isinstance(v, int) else v for k, v in buffing_wheel_payload.items()})
    client.post(f"/v3/projects/{project_id}/product-truth/confirm")
    with sample_image_file.open("rb") as handle:
        client.post(
            f"/v3/projects/{project_id}/assets",
            data={
                "primary_role": "product_identity",
                "must_transfer": "overall geometry,center hole,natural off-white cotton",
                "must_not_transfer": "background,lighting",
                "is_identity_anchor": "true",
                "user_approved": "true",
            },
            files={"asset_file": ("identity.png", handle, "image/png")},
        )
    with sample_image_file.open("rb") as handle:
        client.post(
            f"/v3/projects/{project_id}/assets",
            data={
                "primary_role": "installation",
                "must_transfer": "spindle through center hole,flat washer,nut",
                "must_not_transfer": "background,environment",
                "user_approved": "true",
            },
            files={"asset_file": ("install.png", handle, "image/png")},
        )
    client.post(f"/v3/projects/{project_id}/director-plan/generate")
    client.post(f"/v3/projects/{project_id}/shots/confirm-all")
    shots = [dict(row) for row in db_conn.execute("SELECT * FROM v3_shots WHERE project_id = ? ORDER BY sequence_index", (project_id,)).fetchall()]
    return project_id, shots


def _generation_service():
    return importlib.import_module("clipforge_v3.services.generation_service")


def _review_service():
    return importlib.import_module("clipforge_v3.services.review_service")


def _take_service():
    return importlib.import_module("clipforge_v3.services.take_service")


def _assembly_service():
    return importlib.import_module("clipforge_v3.services.assembly_service")


def _scheduling_service():
    return importlib.import_module("clipforge_v3.services.scheduling_service")


def test_no_dependency_shots_are_parallel():
    scheduling = _scheduling_service()
    shots = [
        {"shot_id": "S01", "sequence_index": 1, "continuity_group": "g1", "depends_on_shot_id": None},
        {"shot_id": "S02", "sequence_index": 2, "continuity_group": "g2", "depends_on_shot_id": None},
    ]
    state = scheduling.compute_schedule_state(shots, set(), set())
    assert state[0]["status"] == "ready_parallel"
    assert state[1]["status"] == "ready_parallel"


def test_dependency_shots_are_serial():
    scheduling = _scheduling_service()
    shots = [
        {"shot_id": "S01", "sequence_index": 1, "continuity_group": "g1", "depends_on_shot_id": None},
        {"shot_id": "S02", "sequence_index": 2, "continuity_group": "g1", "depends_on_shot_id": None},
    ]
    state = scheduling.compute_schedule_state(shots, {"S01"}, set())
    assert state[1]["status"] == "ready_sequential"


def test_cycle_dependency_blocked():
    scheduling = _scheduling_service()
    graph = scheduling.build_dependency_graph(
        [
            {"shot_id": "S01", "sequence_index": 1, "continuity_group": "g1", "depends_on_shot_id": "S02"},
            {"shot_id": "S02", "sequence_index": 2, "continuity_group": "g2", "depends_on_shot_id": "S01"},
        ]
    )
    assert scheduling.detect_cycle(graph)


def test_upstream_selected_take_required_for_downstream_generation(client, db_conn, buffing_wheel_payload, sample_image_file):
    generation = _generation_service()
    take_service = _take_service()
    project_id, shots = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    compile_one = generation.compile_prompt(project_id=project_id, shot_id=shots[0]["id"])
    compile_two = generation.compile_prompt(project_id=project_id, shot_id=shots[1]["id"])
    result = generation.preflight(project_id, shots[1]["id"], compile_two["id"], "draft")
    assert any(item["name"] == "continuity_dependency" and not item["passed"] for item in result["items"])
    first_take = generation.submit_generation(project_id, shots[0]["id"], compile_one["id"], "draft")
    take_service.select_take(project_id, first_take["take_id"])
    result = generation.preflight(project_id, shots[1]["id"], compile_two["id"], "draft")
    assert any(item["name"] == "continuity_dependency" and item["passed"] for item in result["items"])


def test_ffmpeg_extracts_first_last_frames(client, db_conn, buffing_wheel_payload, sample_image_file):
    generation = _generation_service()
    project_id, shots = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    compiled = generation.compile_prompt(project_id=project_id, shot_id=shots[0]["id"])
    take_result = generation.submit_generation(project_id, shots[0]["id"], compiled["id"], "draft")
    take = db_conn.execute("SELECT * FROM v3_takes WHERE id = ?", (take_result["take_id"],)).fetchone()
    assert Path(take["first_frame_path"]).exists()
    assert Path(take["last_frame_path"]).exists()


def test_take_files_do_not_overwrite(client, db_conn, buffing_wheel_payload, sample_image_file):
    generation = _generation_service()
    project_id, shots = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    compiled = generation.compile_prompt(project_id=project_id, shot_id=shots[0]["id"])
    take1 = generation.submit_generation(project_id, shots[0]["id"], compiled["id"], "draft")
    take2 = generation.submit_generation(project_id, shots[0]["id"], compiled["id"], "draft", changed_variable="seed")
    row1 = db_conn.execute("SELECT local_path FROM v3_takes WHERE id = ?", (take1["take_id"],)).fetchone()
    row2 = db_conn.execute("SELECT local_path FROM v3_takes WHERE id = ?", (take2["take_id"],)).fetchone()
    assert row1["local_path"] != row2["local_path"]


def test_reroll_uses_same_prompt(client, db_conn, buffing_wheel_payload, sample_image_file):
    generation = _generation_service()
    project_id, shots = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    compiled = generation.compile_prompt(project_id=project_id, shot_id=shots[0]["id"])
    take1 = generation.submit_generation(project_id, shots[0]["id"], compiled["id"], "draft")
    take2 = generation.submit_generation(project_id, shots[0]["id"], compiled["id"], "draft", parent_take_id=take1["take_id"], changed_variable="seed")
    rows = db_conn.execute("SELECT prompt_version_id FROM v3_takes WHERE id IN (?, ?)", (take1["take_id"], take2["take_id"])).fetchall()
    assert rows[0]["prompt_version_id"] == rows[1]["prompt_version_id"]


def test_rewrite_creates_new_prompt_version(client, db_conn, buffing_wheel_payload, sample_image_file):
    generation = _generation_service()
    project_id, shots = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    compiled1 = generation.compile_prompt(project_id=project_id, shot_id=shots[0]["id"])
    db_conn.execute("UPDATE v3_shots SET subject_action = ? WHERE id = ?", ("Updated install action", shots[0]["id"]))
    db_conn.commit()
    compiled2 = generation.compile_prompt(project_id=project_id, shot_id=shots[0]["id"])
    assert compiled2["id"] != compiled1["id"]


def test_multiple_variable_change_marks_uncontrolled_revision(client, db_conn, buffing_wheel_payload, sample_image_file):
    generation = _generation_service()
    project_id, shots = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    compiled = generation.compile_prompt(project_id=project_id, shot_id=shots[0]["id"])
    take = generation.submit_generation(project_id, shots[0]["id"], compiled["id"], "draft", changed_variable="seed", change_count=2, uncontrolled_revision=True)
    row = db_conn.execute("SELECT uncontrolled_revision FROM v3_takes WHERE id = ?", (take["take_id"],)).fetchone()
    assert row["uncontrolled_revision"] == 1


def test_same_error_twice_stops_reroll(client, db_conn, buffing_wheel_payload, sample_image_file):
    generation = _generation_service()
    review = _review_service()
    project_id, shots = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    compiled = generation.compile_prompt(project_id=project_id, shot_id=shots[0]["id"])
    take1 = generation.submit_generation(project_id, shots[0]["id"], compiled["id"], "draft")
    take2 = generation.submit_generation(project_id, shots[0]["id"], compiled["id"], "draft", changed_variable="seed")
    for take_id in (take1["take_id"], take2["take_id"]):
        review.review_take(
            {
                "take_id": take_id,
                "verdict": "REROLL",
                "product_identity_score": 6,
                "mechanical_accuracy_score": 5,
                "material_accuracy_score": 7,
                "motion_realism_score": 6,
                "camera_execution_score": 7,
                "continuity_score": 7,
                "commercial_usability_score": 6,
                "safety": "pass",
                "error_codes_json": ["M001"],
                "reviewer_notes": "same issue",
                "next_action": "retry",
            }
        )
    plan = review.plan_retake(take2["take_id"])
    assert plan["verdict"] == "REWRITE"


def test_attempt_budget_blocks_extra_take(client, db_conn, buffing_wheel_payload, sample_image_file):
    generation = _generation_service()
    project_id, shots = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    db_conn.execute("UPDATE v3_shots SET max_draft_takes = 1 WHERE id = ?", (shots[0]["id"],))
    db_conn.commit()
    compiled = generation.compile_prompt(project_id=project_id, shot_id=shots[0]["id"])
    generation.submit_generation(project_id, shots[0]["id"], compiled["id"], "draft")
    try:
        generation.submit_generation(project_id, shots[0]["id"], compiled["id"], "draft")
        assert False, "expected budget failure"
    except ValueError as exc:
        assert "budget exceeded" in str(exc)


def test_selected_take_change_invalidates_final_video(client, db_conn, buffing_wheel_payload, sample_image_file):
    generation = _generation_service()
    take_service = _take_service()
    assembly = _assembly_service()
    project_id, shots = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    for shot in shots:
        compiled = generation.compile_prompt(project_id=project_id, shot_id=shot["id"])
        take = generation.submit_generation(project_id, shot["id"], compiled["id"], "draft")
        take_service.select_take(project_id, take["take_id"])
    assembly.rebuild_final_video(project_id)
    db_conn.execute("UPDATE v3_projects SET final_assembly_valid = 1 WHERE id = ?", (project_id,))
    db_conn.commit()
    second_take = generation.submit_generation(project_id, shots[0]["id"], generation.compile_prompt(project_id=project_id, shot_id=shots[0]["id"])["id"], "draft", changed_variable="seed")
    take_service.select_take(project_id, second_take["take_id"])
    project = db_conn.execute("SELECT final_assembly_valid FROM v3_projects WHERE id = ?", (project_id,)).fetchone()
    assert project["final_assembly_valid"] == 0


def test_final_assembly_uses_selected_takes(client, db_conn, buffing_wheel_payload, sample_image_file):
    generation = _generation_service()
    take_service = _take_service()
    assembly_service = _assembly_service()
    project_id, shots = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    selected = []
    for shot in shots:
        compiled = generation.compile_prompt(project_id=project_id, shot_id=shot["id"])
        take = generation.submit_generation(project_id, shot["id"], compiled["id"], "draft")
        take_service.select_take(project_id, take["take_id"])
        selected.append(take["take_id"])
    assembly = assembly_service.rebuild_final_video(project_id)
    assert assembly["assembly_take_ids"] == selected


def test_buffing_wheel_error_codes_drive_retake(client, db_conn, buffing_wheel_payload, sample_image_file):
    generation = _generation_service()
    review = _review_service()
    take_service = _take_service()
    project_id, shots = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    first = generation.compile_prompt(project_id=project_id, shot_id=shots[0]["id"])
    first_take = generation.submit_generation(project_id, shots[0]["id"], first["id"], "draft")
    take_service.select_take(project_id, first_take["take_id"])
    compiled = generation.compile_prompt(project_id=project_id, shot_id=shots[1]["id"])
    take = generation.submit_generation(project_id, shots[1]["id"], compiled["id"], "draft")
    review.review_take(
        {
            "take_id": take["take_id"],
            "verdict": "REWRITE",
            "product_identity_score": 7,
            "mechanical_accuracy_score": 4,
            "material_accuracy_score": 8,
            "motion_realism_score": 6,
            "camera_execution_score": 7,
            "continuity_score": 7,
            "commercial_usability_score": 6,
            "safety": "pass",
            "error_codes_json": ["M001", "M002", "A001"],
            "reviewer_notes": "install relation broken",
            "next_action": "rewrite",
        }
    )
    plan = review.plan_retake(take["take_id"])
    assert plan["changed_variable"] == "one_prompt_clause"


def test_identity_reanchor_interval(client, db_conn, buffing_wheel_payload, sample_image_file, monkeypatch):
    monkeypatch.setenv("V3_IDENTITY_REANCHOR_INTERVAL", "2")
    importlib.reload(importlib.import_module("clipforge_v3.services.continuity_service"))
    generation = _generation_service()
    project_id, shots = _prepare_project(client, db_conn, buffing_wheel_payload, sample_image_file)
    db_conn.execute("UPDATE v3_shots SET continuity_group = 'chain' WHERE project_id = ?", (project_id,))
    db_conn.commit()
    generation.compile_prompt(project_id=project_id, shot_id=shots[0]["id"])
    generation.compile_prompt(project_id=project_id, shot_id=shots[1]["id"])
    compiled3 = generation.compile_prompt(project_id=project_id, shot_id=shots[2]["id"])
    assert compiled3["validation_result_json"]["reanchor_identity"] is True
