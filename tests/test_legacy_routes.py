from __future__ import annotations


def test_v1_homepage_still_opens(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "ClipForge Tools" in response.text


def test_v2_homepage_still_opens(client):
    response = client.get("/v2")
    assert response.status_code == 200
    assert "2.0" in response.text


def test_v2_smoke_create_job_still_works(client, db_conn):
    response = client.post(
        "/v2/jobs",
        data={
            "idea_title": "RV Hitch Polish Storyboard Flow",
            "project_name": "ClipForge 2.0 Smoke",
            "product_name": "Cotton Polishing Wheel Kit",
            "simple_idea": "Create a storyboard-first product demo for a polishing wheel kit.",
            "target_audience": "Amazon tool buyers",
            "video_mode": "long_video",
            "ratio": "9:16",
            "clip_duration": "10",
            "clip_count": "4",
            "resolution": "720p",
            "style_preference": "garage realism",
            "youtube_title": "Smoke Test",
            "youtube_description": "Smoke test description",
            "youtube_account_id": "",
            "privacy": "unlisted",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/v2/jobs/")

    db_conn.execute("SELECT COUNT(*) FROM jobs")
    job_row = db_conn.execute(
        "SELECT workflow_version, project_name, product_name FROM jobs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert job_row["workflow_version"] == "2.0"
    assert job_row["project_name"] == "ClipForge 2.0 Smoke"
    assert job_row["product_name"] == "Cotton Polishing Wheel Kit"
