import base64
import os
import sys
import tempfile
from pathlib import Path


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlH0uQAAAAASUVORK5CYII="
)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _set_temp_env(tmp_root: Path) -> None:
    os.environ["DATA_DIR"] = str(tmp_root / "data")
    os.environ["DB_PATH"] = str(tmp_root / "data" / "clipforge.db")
    os.environ["OUTPUTS_DIR"] = str(tmp_root / "outputs")
    os.environ["UPLOADS_DIR"] = str(tmp_root / "uploads")


def _fake_image_generator(prompt: str, ratio: str, output_path: Path):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(PNG_1X1)
    return {
        "model": "fake-gpt-image-1",
        "usage": {"total_tokens": 321},
        "total_tokens": 321,
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="clipforge_v2_smoke_") as tmp_dir:
        tmp_root = Path(tmp_dir)
        _set_temp_env(tmp_root)

        import task_queue
        from fastapi.testclient import TestClient

        import app as app_module
        from db import (
            create_clip_records,
            get_clip_rows_by_job_id,
            get_job_by_id,
            get_storyboard_frames,
            init_db,
            update_clip_by_job_and_index,
            update_job_fields,
        )

        # Queue operations become no-ops for smoke testing.
        task_queue.enqueue_storyboard_prompts_job = lambda job_id: {"job_id": job_id, "queued": "prompts"}
        task_queue.enqueue_storyboard_images_job = lambda job_id, base_url, frame_id=None: {
            "job_id": job_id,
            "queued": "images",
            "frame_id": frame_id,
        }
        task_queue.enqueue_storyboard_video_job = lambda job_id: {"job_id": job_id, "queued": "video"}
        task_queue.enqueue_publish_job = lambda job_id: {"job_id": job_id, "queued": "publish"}
        task_queue.enqueue_storyboard_single_clip_job = lambda job_id, clip_index: {
            "job_id": job_id,
            "clip_index": clip_index,
            "queued": "single_clip",
        }

        app_module.generate_storyboard_prompts = lambda payload: {
            "frames": [
                {
                    "clip_index": 1,
                    "scene_role": "hook",
                    "prompt_zh": "第一段中文提示词",
                    "prompt_en": "first scene prompt",
                },
                {
                    "clip_index": 2,
                    "scene_role": "demo",
                    "prompt_zh": "第二段中文提示词",
                    "prompt_en": "second scene prompt",
                },
            ],
            "usage": {"input_tokens": 120, "output_tokens": 180, "total_tokens": 300},
        }
        app_module.generate_storyboard_image = _fake_image_generator

        init_db()
        client = TestClient(app_module.app)

        response = client.post(
            "/v2/jobs?lang=zh",
            data={
                "idea_title": "Smoke V2",
                "project_name": "Smoke Project",
                "product_name": "Smoke Tool",
                "simple_idea": "做一个两段式工具短视频",
                "target_audience": "DIY 用户",
                "video_mode": "long_video",
                "ratio": "9:16",
                "clip_duration": "10",
                "clip_count": "2",
                "resolution": "720p",
                "style_preference": "真实车库",
                "youtube_title": "Smoke Title",
                "youtube_description": "Smoke Description",
                "youtube_account_id": "",
                "privacy": "unlisted",
                "upload_to_youtube": "true",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303, response.text
        location = response.headers["location"]
        job_id = int(location.split("/v2/jobs/")[1].split("?")[0])

        app_module.generate_storyboard_prompts_job(job_id)
        job = get_job_by_id(job_id)
        assert job["workflow_stage"] == "prompts_ready"
        assert int(job["prompt_reviewed"] or 0) == 0
        frames = get_storyboard_frames(job_id)
        assert len(frames) == 2

        response = client.post(f"/v2/jobs/{job_id}/generate-images?lang=zh", follow_redirects=False)
        assert response.status_code == 400

        response = client.post(f"/v2/jobs/{job_id}/confirm-prompts?lang=zh", follow_redirects=False)
        assert response.status_code == 303
        job = get_job_by_id(job_id)
        assert int(job["prompt_reviewed"] or 0) == 1

        frame_id = int(frames[0]["id"])
        response = client.post(
            f"/v2/frames/{frame_id}/update-prompts?lang=zh",
            data={"prompt_zh": "更新后的中文提示词", "prompt_en": "updated prompt"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        job = get_job_by_id(job_id)
        assert int(job["prompt_reviewed"] or 0) == 0

        response = client.post(f"/v2/jobs/{job_id}/confirm-prompts?lang=zh", follow_redirects=False)
        assert response.status_code == 303
        job = get_job_by_id(job_id)
        assert int(job["prompt_reviewed"] or 0) == 1

        app_module.generate_storyboard_images_job(job_id, "http://testserver")
        frames = get_storyboard_frames(job_id)
        assert all(frame["image_status"] == "ready" for frame in frames)

        response = client.post(f"/v2/jobs/{job_id}/approve-all?lang=zh", follow_redirects=False)
        assert response.status_code == 303

        response = client.post(f"/v2/jobs/{job_id}/launch-video?lang=zh", follow_redirects=False)
        assert response.status_code == 303

        clips_dir = tmp_root / "outputs" / str(job_id) / "clips"
        clips_dir.mkdir(parents=True, exist_ok=True)
        create_clip_records(job_id, 2)
        for clip_index in (1, 2):
            clip_path = clips_dir / f"clip_{clip_index:02d}.mp4"
            clip_path.write_bytes(b"fake mp4 bytes")
            update_clip_by_job_and_index(
                job_id,
                clip_index,
                {
                    "status": "succeeded",
                    "local_path": str(clip_path),
                    "tokens": 1000 * clip_index,
                    "estimated_cost_cny": 0.1 * clip_index,
                },
            )

        final_video_path = tmp_root / "outputs" / str(job_id) / "final_video.mp4"
        final_video_path.parent.mkdir(parents=True, exist_ok=True)
        final_video_path.write_bytes(b"fake final mp4")
        update_job_fields(
            job_id,
            {
                "workflow_stage": "videos_ready",
                "final_video_path": str(final_video_path),
                "status": "succeeded",
                "current_step": "Video generation completed",
            },
        )

        response = client.post(f"/v2/jobs/{job_id}/publish?lang=zh", follow_redirects=False)
        assert response.status_code == 400

        response = client.post(f"/v2/jobs/{job_id}/confirm-video?lang=zh", follow_redirects=False)
        assert response.status_code == 303
        job = get_job_by_id(job_id)
        assert int(job["video_reviewed"] or 0) == 1

        response = client.post(f"/v2/jobs/{job_id}/confirm-publish?lang=zh", follow_redirects=False)
        assert response.status_code == 303
        job = get_job_by_id(job_id)
        assert int(job["publish_confirmed"] or 0) == 1

        response = client.post(f"/v2/jobs/{job_id}/publish?lang=zh", follow_redirects=False)
        assert response.status_code == 303

        clips = get_clip_rows_by_job_id(job_id)
        assert len(clips) == 2
        response = client.post(f"/v2/jobs/{job_id}/clips/1/regenerate?lang=zh", follow_redirects=False)
        assert response.status_code == 303

        print("v2 workflow smoke test passed")


if __name__ == "__main__":
    main()
