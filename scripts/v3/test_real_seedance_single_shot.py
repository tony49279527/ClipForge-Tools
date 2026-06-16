from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REQUIRED_CONFIRM = "I_UNDERSTAND_THIS_COSTS_MONEY"


def _env_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _guard() -> None:
    failures = []
    if os.getenv("V3_VIDEO_PROVIDER", "mock").strip().lower() != "ark":
        failures.append("V3_VIDEO_PROVIDER must be ark")
    if not _env_enabled("V3_REAL_API_ENABLED"):
        failures.append("V3_REAL_API_ENABLED must be true")
    if os.getenv("V3_REAL_API_TEST_CONFIRM") != REQUIRED_CONFIRM:
        failures.append(f"V3_REAL_API_TEST_CONFIRM must be {REQUIRED_CONFIRM}")
    if failures:
        print("Refusing to run paid Seedance test.")
        for item in failures:
            print(f"- {item}")
        print("This script is intentionally disabled by default.")
        raise SystemExit(2)


def _create_identity_asset(asset_service, project_id: int) -> None:
    output_dir = Path(os.getenv("UPLOADS_DIR", REPO_ROOT / "uploads")) / "v3_real_api_test"
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / f"buffing_wheel_identity_{project_id}.png"
    if not image_path.exists():
        Image.new("RGB", (1600, 1600), color=(240, 236, 220)).save(image_path)
    asset_service.create_asset(
        project_id=project_id,
        file_path=image_path,
        original_filename=image_path.name,
        primary_role="product_identity",
        secondary_role=None,
        must_transfer=["overall geometry", "center hole", "concentric stitched rings", "natural off-white cotton"],
        must_not_transfer=["background", "camera angle", "lighting"],
        applies_to_shots=["S01", "S02", "S03"],
        is_identity_anchor=True,
        user_approved=True,
        mime_type="image/png",
        storage_backend="local",
        access_url=None,
    )


def main() -> int:
    _guard()

    os.environ.setdefault("CLIPFORGE_V3_ENABLED", "true")
    os.environ.setdefault("SEEDANCE_DEFAULT_RESOLUTION", "720p")
    os.environ.setdefault("V3_PROVIDER_POLL_TIMEOUT_SECONDS", "900")
    os.environ.setdefault("V3_PROVIDER_POLL_INTERVAL_SECONDS", "5")
    # Manual one-shot CLI keeps the same service path but processes synchronously
    # so the operator can see whether a take was created.
    os.environ["V3_SYNC_PROVIDER_TASK_FOR_TESTS"] = "true"

    import app
    from clipforge_v3.schemas.project import V3ProjectCreate
    from clipforge_v3.services import asset_service, generation_service, product_truth_service, project_service

    app.on_startup()
    fixture = json.loads((REPO_ROOT / "tests" / "fixtures" / "buffing_wheel.json").read_text(encoding="utf-8"))
    fixture["resolution"] = "720p"
    fixture["default_clip_duration"] = 5
    detail = project_service.create_project(V3ProjectCreate(**fixture))
    project_id = detail["project"]["id"]
    product_truth_service.confirm_latest_product_truth(project_id)
    _create_identity_asset(asset_service, project_id)
    project_service.generate_director_plan(project_id)
    project_service.confirm_shot_contracts(project_id)
    detail = project_service.get_project_detail(project_id)
    shot = detail["shots"][0]

    compiled = generation_service.compile_prompt(project_id=project_id, shot_id=shot["id"])
    generation_service.lock_prompt(compiled["id"])
    confirmation = generation_service.build_paid_confirmation(
        project_id=project_id,
        shot_id=shot["id"],
        prompt_version_id=compiled["id"],
        tier="draft",
    )

    print("About to submit one real paid Seedance draft generation.")
    print(f"Project ID: {project_id}")
    print(f"Shot: {confirmation['shot']['shot_id']} / DB {confirmation['shot']['id']}")
    print(f"Provider: {confirmation['provider']} / {confirmation['model']}")
    print(f"Duration: {confirmation['duration']}s")
    print(f"Resolution: {confirmation['resolution']}")
    print(f"Reference assets: {len(confirmation['reference_assets'])}")
    print(f"Prompt version: {confirmation['prompt_version_id']}")
    print(f"Estimated cost: {confirmation['estimated_cost']} CNY estimate")
    print(f"Idempotency key prefix: {confirmation['idempotency_key_prefix']}")
    print("Type exactly YES_PAY_SEEDANCE_ONCE to continue:")
    typed = input("> ").strip()
    if typed != "YES_PAY_SEEDANCE_ONCE":
        print("Cancelled before paid API submission.")
        return 3

    result = generation_service.submit_generation(
        project_id=project_id,
        shot_id=shot["id"],
        prompt_version_id=compiled["id"],
        tier="draft",
        paid_confirmed=True,
        paid_confirmation_token=confirmation["confirmation_token"],
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result.get("submission_status") == "unknown_submission_state":
        print("WARNING: Provider submission state is unknown. Cost may already have been incurred. Do not retry automatically.")
        return 4
    if result.get("take_id"):
        print(f"Take ID: {result['take_id']}")
    else:
        print("No Take ID yet. Check worker/submission status before retrying.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
