from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _url_filename(image_url: str) -> str:
    name = Path(urlparse(image_url).path).name
    return name or "remote-product-reference.jpg"


def _create_identity_asset(project_id: int, image_url: str) -> None:
    from clipforge_v3.repositories import asset_repository

    asset_repository.create_asset(
        {
            "project_id": project_id,
            "asset_type": "image",
            "original_filename": _url_filename(image_url),
            "local_path": None,
            "remote_url": image_url,
            "mime_type": "image/jpeg",
            "primary_role": "product_identity",
            "secondary_role": None,
            "must_transfer_json": ["overall geometry", "center hole", "concentric stitched rings", "natural off-white cotton"],
            "must_not_transfer_json": ["background", "camera angle", "lighting"],
            "applies_to_shots_json": ["S01", "S02", "S03"],
            "is_identity_anchor": True,
            "user_approved": True,
            "metadata_json": {"source": "V3_REAL_TEST_IMAGE_URL", "remote": True},
            "audit_report_json": {
                "width": 0,
                "height": 0,
                "format": "remote",
                "file_size_bytes": 0,
                "is_clear": True,
                "has_transparent_background": False,
                "has_perspective_distortion": False,
                "key_structure_visible": True,
                "conflict_detected": False,
                "warnings": [],
                "missing_project_angles": [],
            },
            "storage_backend": "remote_url",
            "access_url": None,
        }
    )


def build_payload_summary(image_url: str) -> dict:
    os.environ.setdefault("CLIPFORGE_V3_ENABLED", "true")
    os.environ.setdefault("V3_VIDEO_PROVIDER", "ark")
    os.environ.setdefault("SEEDANCE_DEFAULT_RESOLUTION", "720p")

    import app
    from clipforge_v3.schemas.project import V3ProjectCreate
    from clipforge_v3.services import generation_service, product_truth_service, project_service
    from clipforge_v3.services.provider_asset_resolver import safe_url_preview

    app.on_startup()
    fixture = json.loads((REPO_ROOT / "tests" / "fixtures" / "buffing_wheel.json").read_text(encoding="utf-8"))
    fixture["resolution"] = "720p"
    fixture["default_clip_duration"] = 5
    detail = project_service.create_project(V3ProjectCreate(**fixture))
    project_id = detail["project"]["id"]
    product_truth_service.confirm_latest_product_truth(project_id)
    _create_identity_asset(project_id, image_url)
    project_service.generate_director_plan(project_id)
    project_service.confirm_shot_contracts(project_id)
    detail = project_service.get_project_detail(project_id)
    shot = detail["shots"][0]
    compiled = generation_service.compile_prompt(project_id=project_id, shot_id=shot["id"])
    generation_service.lock_prompt(compiled["id"])
    preflight = generation_service.preflight(project_id, shot["id"], compiled["id"], "draft")
    confirmation = generation_service.build_paid_confirmation(
        project_id=project_id,
        shot_id=shot["id"],
        prompt_version_id=compiled["id"],
        tier="draft",
    )
    payload = compiled["provider_payload_json"]
    image_refs = [entry for entry in payload.get("content", []) if entry.get("type") == "image_url"]
    first_ref = image_refs[0] if image_refs else {}
    url_preview = safe_url_preview(image_url)
    return {
        "provider": confirmation["provider"],
        "model": payload.get("model"),
        "mode": payload.get("mode"),
        "shot": confirmation["shot_id"],
        "prompt_chars": compiled["prompt_char_count"],
        "duration": payload.get("duration"),
        "resolution": payload.get("resolution"),
        "reference_count": len(image_refs),
        "reference_1_role": first_ref.get("reference_role", ""),
        "reference_1_source_type": first_ref.get("type", ""),
        "reference_1_host": url_preview.get("host", ""),
        "reference_1_has_actual_image_url": bool(first_ref.get("image_url", {}).get("url")),
        "preflight_passed": bool(preflight["allow_submit"]),
        "ready_for_paid_submission": bool(preflight["allow_submit"] and first_ref.get("image_url", {}).get("url")),
        "estimated_cost": confirmation["estimated_cost"],
        "idempotency_prefix": confirmation["idempotency_key_prefix"],
        "payload": payload,
    }


def print_summary(summary: dict) -> None:
    print(f"Provider: {summary['provider']}")
    print(f"Model: {summary['model']}")
    print(f"Mode: {summary['mode']}")
    print(f"Shot: {summary['shot']}")
    print(f"Prompt chars: {summary['prompt_chars']}")
    print(f"Duration: {summary['duration']}")
    print(f"Resolution: {summary['resolution']}")
    print(f"Reference count: {summary['reference_count']}")
    print(f"Reference 1 role: {summary['reference_1_role']}")
    print(f"Reference 1 source type: {summary['reference_1_source_type']}")
    print(f"Reference 1 host: {summary['reference_1_host']}")
    print(f"Reference 1 has actual image URL: {str(summary['reference_1_has_actual_image_url']).lower()}")
    print(f"Preflight passed: {str(summary['preflight_passed']).lower()}")
    print(f"Ready for paid submission: {str(summary['ready_for_paid_submission']).lower()}")


def main() -> int:
    from clipforge_v3.services.provider_asset_resolver import validate_public_https_url

    image_url = os.getenv("V3_REAL_TEST_IMAGE_URL", "").strip()
    valid, reason = validate_public_https_url(image_url)
    if not valid:
        print(f"Missing or invalid V3_REAL_TEST_IMAGE_URL: {reason}. Provide a public https:// product image URL.")
        return 2
    summary = build_payload_summary(image_url)
    print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
