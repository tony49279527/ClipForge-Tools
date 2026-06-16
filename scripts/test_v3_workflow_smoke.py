from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="clipforge-v3-smoke-") as tmp:
        root = Path(tmp)
        os.environ.setdefault("CLIPFORGE_V3_ENABLED", "true")
        os.environ["DATA_DIR"] = str(root / "data")
        os.environ["DB_PATH"] = str(root / "data" / "clipforge.db")
        os.environ["OUTPUTS_DIR"] = str(root / "outputs")
        os.environ["UPLOADS_DIR"] = str(root / "uploads")
        os.environ.setdefault("SEEDANCE_PROVIDER", "ark")
        os.environ.setdefault("SEEDANCE_MODEL", "mock-seedance")
        os.environ.setdefault("SEEDANCE_BASE_URL", "https://example.invalid")

        from app import app, on_startup
        from clipforge_v3.services import generation_service, review_service, take_service
        from clipforge_v3.services.assembly_service import rebuild_final_video
        from db import get_conn

        on_startup()
        fixture_path = Path("tests/fixtures/buffing_wheel.json")
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        client = TestClient(app)
        response = client.post("/v3/projects", data={k: str(v) for k, v in payload.items()}, follow_redirects=False)
        assert response.status_code == 303, response.text
        project_id = int(response.headers["location"].rsplit("/", 1)[-1])

        assert client.post(f"/v3/projects/{project_id}/product-truth/confirm", follow_redirects=False).status_code == 303

        image_path = root / "identity.png"
        Image.new("RGB", (1600, 1600), color=(240, 236, 220)).save(image_path)
        with image_path.open("rb") as handle:
            response = client.post(
                f"/v3/projects/{project_id}/assets",
                data={
                    "primary_role": "product_identity",
                    "must_transfer": "overall geometry,center hole,stitched rings,natural off-white cotton",
                    "must_not_transfer": "background,watermark",
                    "is_identity_anchor": "true",
                    "user_approved": "true",
                },
                files={"asset_file": ("identity.png", handle, "image/png")},
                follow_redirects=False,
            )
        assert response.status_code == 303, response.text

        assert client.post(f"/v3/projects/{project_id}/director-plan/generate", follow_redirects=False).status_code == 303
        assert client.post(f"/v3/projects/{project_id}/shots/confirm-all", follow_redirects=False).status_code == 303

        conn = get_conn()
        shots = [dict(row) for row in conn.execute("SELECT * FROM v3_shots WHERE project_id = ? ORDER BY sequence_index", (project_id,)).fetchall()]
        assert shots, "Director Plan did not create shots"

        selected = []
        for shot in shots:
            compiled = generation_service.compile_prompt(project_id=project_id, shot_id=shot["id"])
            generation_service.lock_prompt(compiled["id"])
            preflight = generation_service.preflight(project_id, shot["id"], compiled["id"], "draft")
            assert preflight["allow_submit"], preflight
            take = generation_service.submit_generation(project_id, shot["id"], compiled["id"], "draft")
            review_service.review_take(
                {
                    "take_id": take["take_id"],
                    "verdict": "KEEP",
                    "product_identity_score": 8,
                    "mechanical_accuracy_score": 8,
                    "material_accuracy_score": 8,
                    "motion_realism_score": 7,
                    "camera_execution_score": 7,
                    "continuity_score": 7,
                    "commercial_usability_score": 8,
                    "safety": "pass",
                    "error_codes_json": [],
                    "reviewer_notes": "smoke accepted",
                    "next_action": "select take",
                }
            )
            take_service.select_take(project_id, take["take_id"])
            selected.append(take["take_id"])

        first = shots[0]
        compiled = generation_service.compile_prompt(project_id=project_id, shot_id=first["id"])
        production_preflight = generation_service.preflight(project_id, first["id"], compiled["id"], "production")
        assert production_preflight["allow_submit"], production_preflight
        generation_service.submit_generation(project_id, first["id"], compiled["id"], "production", changed_variable="resolution")

        assembly = rebuild_final_video(project_id)
        assert assembly["assembly_take_ids"] == selected
        gate = client.get(f"/v3/projects/{project_id}/publish-gate").json()
        assert gate["allow_publish"] is True, gate
        print(json.dumps({"project_id": project_id, "shots": len(shots), "selected_takes": selected, "assembly": assembly["output_path"]}, ensure_ascii=False))
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
