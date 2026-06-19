from __future__ import annotations

from pathlib import Path

from clipforge_v3.providers import seedance_ark
from clipforge_v3.services import generation_service


def _extract_project_id(location: str) -> int:
    return int(location.split("project_id=")[-1].split("&", 1)[0])


def test_v3_demo_homepage_shows_guided_mock_flow(client):
    response = client.get("/v3")
    assert response.status_code == 200
    assert "Demo mode only." in response.text
    assert "Step 1 · Project" in response.text
    assert "Step 2 · Product Info / Image" in response.text
    assert "Step 3 · Prompt" in response.text
    assert "Step 4 · Mock Generate" in response.text
    assert "Step 5 · Result / Take" in response.text


def test_v3_demo_homepage_uses_database_url_for_backend_label(client, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:secret@example.internal/clipforge")

    response = client.get("/v3")

    assert response.status_code == 200
    assert "Database: PostgreSQL" in response.text
    assert "secret" not in response.text


def test_v3_demo_flow_creates_mock_take_without_ark(client, db_conn, monkeypatch):
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "mock")
    monkeypatch.setenv("V3_REAL_API_ENABLED", "false")

    class GuardProvider(seedance_ark.ArkSeedanceProvider):
        def submit_task(self, payload):
            raise AssertionError("mock demo flow must not call Ark submit_task")

    monkeypatch.setattr(seedance_ark, "get_provider", lambda: GuardProvider())
    monkeypatch.setattr(generation_service, "get_provider", lambda: GuardProvider())

    create_response = client.post(
        "/v3/demo/projects",
        data={"use_demo_product": "true"},
        follow_redirects=False,
    )
    assert create_response.status_code == 303
    project_id = _extract_project_id(create_response.headers["location"])

    save_response = client.post(
        f"/v3/demo/projects/{project_id}/product-info",
        data={
            "source_description": "6 inch stitched cotton buffing wheel, 1 inch thickness, 1/2 inch arbor hole, used for final polishing.",
            "dimensions_input": "6 inch diameter, 1 inch thickness, 1/2 inch arbor hole",
            "materials_input": "natural off-white stitched cotton",
            "working_surface_input": "outer cotton circumference",
            "parts_summary": "70 ply stitched rings",
            "target_marketplace": "Amazon US",
            "video_style": "clean demo",
            "confirm_truth": "true",
        },
        follow_redirects=False,
    )
    assert save_response.status_code == 303

    asset_response = client.post(f"/v3/demo/projects/{project_id}/assets/sample", follow_redirects=False)
    assert asset_response.status_code == 303

    prompt_response = client.post(f"/v3/demo/projects/{project_id}/prompt", follow_redirects=False)
    assert prompt_response.status_code == 303

    generate_response = client.post(f"/v3/demo/projects/{project_id}/generate", follow_redirects=False)
    assert generate_response.status_code == 303

    page = client.get(f"/v3?project_id={project_id}")
    assert page.status_code == 200
    assert "Latest mock result" in page.text
    assert "Provider:</strong> mock" in page.text
    assert "Cost:</strong> 0" in page.text or "Cost:</strong> 0.0" in page.text

    take = db_conn.execute(
        "SELECT seedance_task_id, estimated_cost, status, local_path FROM v3_takes ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert take is not None
    assert take["seedance_task_id"].startswith("mock-")
    assert float(take["estimated_cost"]) == 0
    assert take["status"] == "completed"
    assert Path(take["local_path"]).exists()

    usage = db_conn.execute(
        "SELECT provider, estimated_cost FROM v3_usage_events WHERE project_id = ? ORDER BY id DESC LIMIT 1",
        (project_id,),
    ).fetchone()
    assert usage["provider"] == "mock"
    assert float(usage["estimated_cost"]) == 0


def test_v3_demo_take_preview_route_serves_mock_video(client, db_conn, monkeypatch):
    monkeypatch.setenv("V3_VIDEO_PROVIDER", "mock")
    monkeypatch.setenv("V3_REAL_API_ENABLED", "false")
    project_id = _extract_project_id(client.post("/v3/demo/projects", data={"use_demo_product": "true"}, follow_redirects=False).headers["location"])
    client.post(
        f"/v3/demo/projects/{project_id}/product-info",
        data={
            "source_description": "Buffing wheel with confirmed dimensions and working surface.",
            "dimensions_input": "6 inch, 1 inch, 1/2 inch",
            "materials_input": "stitched cotton",
            "working_surface_input": "outer cotton circumference",
            "parts_summary": "stitched rings",
            "target_marketplace": "Amazon",
            "video_style": "clean demo",
            "confirm_truth": "true",
        },
    )
    client.post(f"/v3/demo/projects/{project_id}/assets/sample")
    client.post(f"/v3/demo/projects/{project_id}/prompt")
    client.post(f"/v3/demo/projects/{project_id}/generate")
    take_id = db_conn.execute("SELECT id FROM v3_takes ORDER BY id DESC LIMIT 1").fetchone()["id"]
    preview = client.get(f"/v3/takes/{take_id}/video")
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("video/mp4")
