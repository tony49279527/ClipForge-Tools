from __future__ import annotations

import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _create_project(client, payload: dict) -> int:
    response = client.post("/v3/projects", data={k: str(v) for k, v in payload.items()}, follow_redirects=False)
    assert response.status_code == 303
    return int(response.headers["location"].rsplit("/", 1)[-1])


def _prepare_project(client, buffing_wheel_payload, sample_image_file) -> int:
    project_id = _create_project(client, buffing_wheel_payload)
    client.post(f"/v3/projects/{project_id}/product-truth/confirm")
    with sample_image_file.open("rb") as handle:
        response = client.post(
            f"/v3/projects/{project_id}/assets",
            data={
                "primary_role": "product_identity",
                "must_transfer": "overall geometry,center hole,stitched rings",
                "must_not_transfer": "background",
                "is_identity_anchor": "true",
                "user_approved": "true",
            },
            files={"asset_file": ("identity.png", handle, "image/png")},
            follow_redirects=False,
        )
    assert response.status_code == 303
    client.post(f"/v3/projects/{project_id}/director-plan/generate")
    return project_id


def test_v3_console_contains_operator_surfaces(client, buffing_wheel_payload, sample_image_file):
    project_id = _prepare_project(client, buffing_wheel_payload, sample_image_file)
    response = client.get(f"/v3/projects/{project_id}")
    assert response.status_code == 200
    assert "工作流 Stepper" in response.text
    assert "Shot Board" in response.text
    assert "Reference Assets" in response.text
    assert "Prompt Inspector" in response.text
    assert "Take Review Studio" in response.text
    assert "成本中心" in response.text


def test_health_ready_do_not_leak_secrets(client, monkeypatch):
    monkeypatch.setenv("SEEDANCE_API_KEY", "sk-test-secret-value")
    health = client.get("/v3/health")
    ready = client.get("/v3/ready")
    assert health.status_code == 200
    assert ready.status_code == 200
    combined = health.text + ready.text
    assert "sk-test-secret-value" not in combined
    assert "api_key_configured" in ready.text


def test_storage_rejects_unsupported_upload(client, buffing_wheel_payload, tmp_path):
    project_id = _create_project(client, buffing_wheel_payload)
    bad_file = tmp_path / "malware.exe"
    bad_file.write_bytes(b"MZ fake")
    with bad_file.open("rb") as handle:
        response = client.post(
            f"/v3/projects/{project_id}/assets",
            data={"primary_role": "product_identity"},
            files={"asset_file": ("malware.exe", handle, "application/x-msdownload")},
        )
    assert response.status_code == 400
    assert "supported file" in response.text


def test_local_storage_route_blocks_traversal(client):
    response = client.get("/v3/storage/local/1/..%2Fsecret.txt")
    assert response.status_code in {400, 404}


def test_shot_board_copy_split_disable(client, db_conn, buffing_wheel_payload, sample_image_file):
    project_id = _prepare_project(client, buffing_wheel_payload, sample_image_file)
    shot = db_conn.execute("SELECT id FROM v3_shots WHERE project_id = ? ORDER BY sequence_index LIMIT 1", (project_id,)).fetchone()
    assert client.post(f"/v3/projects/{project_id}/shots/{shot['id']}/copy", follow_redirects=False).status_code == 303
    assert client.post(f"/v3/projects/{project_id}/shots/{shot['id']}/split", follow_redirects=False).status_code == 303
    assert client.post(f"/v3/projects/{project_id}/shots/{shot['id']}/disable", follow_redirects=False).status_code == 303
    disabled = db_conn.execute("SELECT status FROM v3_shots WHERE id = ?", (shot["id"],)).fetchone()
    assert disabled["status"] == "disabled"
    count = db_conn.execute("SELECT COUNT(*) AS count FROM v3_shots WHERE project_id = ?", (project_id,)).fetchone()["count"]
    assert count >= 6


def test_director_eval_schema_and_runner():
    spec = importlib.util.spec_from_file_location("run_evals", REPO_ROOT / "evaluation" / "run_evals.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    result = module.run(REPO_ROOT / "evaluation" / "cases" / "director_cases.json")
    assert result["total"] == 20
    assert result["failures"] == []


def test_v3_workflow_smoke_script_runs(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)
    spec = importlib.util.spec_from_file_location("v3_smoke", REPO_ROOT / "scripts" / "test_v3_workflow_smoke.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.main() == 0
