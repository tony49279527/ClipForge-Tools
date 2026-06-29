from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REQUIRED_FIELDS = {
    "id",
    "input",
    "expected_mode",
    "expected_shot_count_range",
    "required_constraints",
    "forbidden_output",
    "expected_warnings",
    "expected_error_codes",
}

CALLED_FUNCTIONS: list[str] = []


def record(name: str):
    CALLED_FUNCTIONS.append(name)


def load_cases(path: Path) -> list[dict]:
    cases = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError("Eval cases must be a JSON list")
    for case in cases:
        missing = REQUIRED_FIELDS.difference(case)
        if missing:
            raise ValueError(f"{case.get('id', '<unknown>')} missing fields: {sorted(missing)}")
    return cases


def _clear_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name == "db" or name.startswith("clipforge_v3"):
            sys.modules.pop(name, None)


def _setup_env(tmp: Path):
    os.environ["DATA_DIR"] = str(tmp / "data")
    os.environ["DB_PATH"] = str(tmp / "data" / "clipforge.db")
    os.environ["OUTPUTS_DIR"] = str(tmp / "outputs")
    os.environ["UPLOADS_DIR"] = str(tmp / "uploads")
    os.environ["CLIPFORGE_V3_ENABLED"] = "true"
    os.environ["V3_VIDEO_PROVIDER"] = "mock"
    os.environ["V3_REAL_API_ENABLED"] = "false"
    _clear_modules()
    app = importlib.import_module("app")
    app.on_startup()
    return SimpleNamespace(
        app=app,
        db=importlib.import_module("db"),
        product_truth_service=importlib.import_module("clipforge_v3.services.product_truth_service"),
        project_service=importlib.import_module("clipforge_v3.services.project_service"),
        asset_service=importlib.import_module("clipforge_v3.services.asset_service"),
        generation_service=importlib.import_module("clipforge_v3.services.generation_service"),
        review_service=importlib.import_module("clipforge_v3.services.review_service"),
        take_service=importlib.import_module("clipforge_v3.services.take_service"),
        mode_router=importlib.import_module("clipforge_v3.director.mode_router"),
        reference_mapper=importlib.import_module("clipforge_v3.director.reference_mapper"),
        fidelity_allocator=importlib.import_module("clipforge_v3.director.fidelity_allocator"),
        shot_planner=importlib.import_module("clipforge_v3.director.shot_planner"),
        prompt_compiler=importlib.import_module("clipforge_v3.compiler.prompt_compiler"),
        preflight=importlib.import_module("clipforge_v3.compiler.preflight"),
        project_repository=importlib.import_module("clipforge_v3.repositories.project_repository"),
        shot_repository=importlib.import_module("clipforge_v3.repositories.shot_repository"),
    )


def _payload() -> dict:
    return json.loads((REPO_ROOT / "tests" / "fixtures" / "buffing_wheel.json").read_text(encoding="utf-8"))


def _create_project(ctx, payload: dict) -> int:
    schema = importlib.import_module("clipforge_v3.schemas.project")
    record = ctx.project_service.create_project(schema.V3ProjectCreate(**payload))
    record_project = record["project"]
    return record_project["id"]


def _confirm_and_asset(ctx, tmp: Path, project_id: int) -> None:
    record("product_truth_service.confirm_latest_product_truth")
    importlib.import_module("clipforge_v3.services.product_truth_service").confirm_latest_product_truth(project_id)
    image_path = tmp / f"identity_{project_id}.png"
    Image.new("RGB", (1600, 1600), color=(240, 236, 220)).save(image_path)
    record("asset_service.create_asset")
    ctx.asset_service.create_asset(
        project_id=project_id,
        file_path=image_path,
        original_filename=image_path.name,
        primary_role="product_identity",
        secondary_role=None,
        must_transfer=["overall geometry", "center hole", "concentric stitched rings", "natural off-white color"],
        must_not_transfer=["background", "lighting", "camera angle"],
        applies_to_shots=["S01", "S02", "S03"],
        is_identity_anchor=True,
        user_approved=True,
        mime_type="image/png",
        storage_backend="local",
        access_url=None,
    )


def _plan_project(ctx, tmp: Path, with_asset: bool = True) -> tuple[int, dict]:
    payload = _payload()
    project_id = _create_project(ctx, payload)
    if with_asset:
        _confirm_and_asset(ctx, tmp, project_id)
    else:
        importlib.import_module("clipforge_v3.services.product_truth_service").confirm_latest_product_truth(project_id)
    record("project_service.generate_director_plan")
    if with_asset:
        ctx.project_service.generate_director_plan(project_id)
    return project_id, ctx.project_service.get_project_detail(project_id)


def _compile_first(ctx, project_id: int, detail: dict) -> dict:
    shot = detail["shots"][0]
    record("generation_service.compile_prompt")
    compiled = ctx.generation_service.compile_prompt(project_id=project_id, shot_id=shot["id"])
    ctx.generation_service.lock_prompt(compiled["id"])
    return compiled


def run(path: Path) -> dict:
    failures: list[str] = []
    cases = load_cases(path)
    with tempfile.TemporaryDirectory(prefix="clipforge-v3-evals-") as tmp_dir:
        tmp = Path(tmp_dir)
        ctx = _setup_env(tmp)
        payload = _payload()

        record("product_truth_service.extract_product_truth_payload")
        truth = ctx.product_truth_service.extract_product_truth_payload(importlib.import_module("clipforge_v3.schemas.product_truth").ProductTruthInput(
            product_name=payload["product_name"],
            product_category=payload["product_category"],
            source_description=payload["source_description"],
            product_url=payload["product_url"],
            dimensions=payload["dimensions_input"],
            materials=payload["materials_input"],
            package_quantity=payload["package_quantity"],
            parts_summary=payload["parts_summary"],
            installation_method=payload["installation_method"],
            working_surface=payload["working_surface_input"],
            intended_for=payload["intended_for"],
            not_for=payload["not_for"],
            safety_notes=payload["safety_notes"],
        ))
        if truth["immutable_geometry"]["thickness"] != "1-inch":
            failures.append("buffing_wheel_dimensions: missing thickness")
        if truth["immutable_geometry"]["center_hole"] != "1/2-inch":
            failures.append("buffing_wheel_dimensions: missing center hole")

        project_id, detail = _plan_project(ctx, tmp, with_asset=True)
        ctx.project_service.confirm_shot_contracts(project_id)
        detail = ctx.project_service.get_project_detail(project_id)
        shots = detail["shots"]
        purposes = {shot["purpose"] for shot in shots}
        if not {"installation_relationship_proof", "working_surface_proof"}.issubset(purposes):
            failures.append("shot_split: installation and working surface were not split")
        if any("install" in shot["subject_action"].lower() and "polish" in shot["subject_action"].lower() for shot in shots):
            failures.append("single_action: install and polish are in one shot")
        if any("," in str(shot["camera_contract_json"].get("movement", "")) for shot in shots):
            failures.append("single_camera: overloaded camera movement")
        if not any(shot.get("depends_on_shot_id") for shot in shots[1:]):
            failures.append("continuity: no sequential dependency")

        compiled = _compile_first(ctx, project_id, detail)
        prompt_text = compiled["prompt_text"]
        if "Preserve image-anchored product identity" not in prompt_text:
            failures.append("i2v_identity: missing identity preservation")
        if len(prompt_text) > 2000:
            failures.append("prompt_budget: prompt exceeds 2000 chars")
        if "text overlays" not in prompt_text:
            failures.append("post_text: text/CTA constraint missing")

        record("mode_router.choose_generation_mode")
        mode = ctx.mode_router.choose_generation_mode(
            shot_purpose="polishing_motion",
            strict_identity=True,
            assets=[
                {"primary_role": "product_identity", "asset_type": "image"},
                {"primary_role": "motion", "asset_type": "video"},
            ],
            needs_continuity=False,
        )
        if mode["selected_mode"] != "R2V":
            failures.append("r2v_mode: expected R2V with identity image plus motion video")

        record("reference_mapper.build_reference_role_map")
        role_map = ctx.reference_mapper.build_reference_role_map(
            shot_purpose="polishing_motion",
            assets=[{"id": 1, "primary_role": "product_identity", "must_transfer_json": ["overall geometry"], "must_not_transfer_json": ["background"]}],
        )
        if not role_map["assets"] or role_map["assets"][0]["must_not_transfer"] != ["background"]:
            failures.append("r2v_roles: role map did not preserve exclusions")

        record("prompt_compiler.detect_conflicts")
        bad_input = ctx.prompt_compiler.CompilerInput(
            project={"product_name": "wheel"},
            shot={**shots[0], "risk_codes_json": [], "single_visible_beat": "one beat", "camera_contract_json": {"movement": "locked"}},
            product_truth={"user_approved": True, "product_truth_json": truth},
            role_map={"assets": [{"primary_role": "product_identity", "must_transfer": ["overall geometry"], "must_not_transfer": []}]},
            continuity_state={},
            mode="I2V",
            provider_capabilities={"supported": True},
            user_constraints=[],
        )
        issues = ctx.prompt_compiler.detect_conflicts(bad_input, "Use felt material. endpoint product identity overall geometry")
        if not any(issue.code == "FORBIDDEN_MATERIAL_CONFLICT" for issue in issues):
            failures.append("truth_conflict: forbidden material did not block")

        missing_project_id, missing_detail = _plan_project(ctx, tmp, with_asset=False)
        missing_shot = ctx.shot_repository.create_shot(
            {
                "project_id": missing_project_id,
                "sequence_index": 1,
                "shot_id": "S01",
                "purpose": "identity_test",
                "mode": "I2V",
                "duration": 5,
                "primary_spend": "product_identity",
                "subject_action": "show product",
                "single_visible_beat": "identity",
                "generation_strategy": "parallel",
                "status": "planned",
                "economized_json": [],
                "start_state_json": {},
                "end_state_json": {},
                "camera_contract_json": {"movement": "locked"},
                "lighting_contract_json": {},
                "audio_contract_json": {},
                "reference_roles_json": [],
                "continuity_anchors_json": {},
                "constraints_json": [],
                "risk_codes_json": [],
                "mode_decision_json": {},
                "fidelity_json": {},
            }
        )
        provider_caps = {"supported": False, "missing": ["product_identity"]}
        prompt_version = {"allow_submit": False, "prompt_char_count": 100, "provider_payload_json": {"content": [{"role": "product_identity"}]}}
        record("preflight.run_preflight")
        preflight_result = ctx.preflight.run_preflight(
            project=missing_detail["project"],
            shot={**ctx.project_service.get_project_detail(missing_project_id)["shots"][0], "id": missing_shot},
            product_truth=ctx.product_truth_service.get_latest_product_truth(missing_project_id),
            assets=[],
            prompt_version=prompt_version,
            provider_capabilities=provider_caps,
            tier="draft",
            dependency_complete=True,
        )
        if preflight_result["allow_submit"]:
            failures.append("missing_identity_preflight: missing product_identity did not block")

        record("generation_service.preflight")
        second_shot = shots[1]
        second_compiled = ctx.generation_service.compile_prompt(project_id=project_id, shot_id=second_shot["id"])
        dep_preflight = ctx.generation_service.preflight(project_id, second_shot["id"], second_compiled["id"], "draft")
        if not any(item["name"] == "continuity_dependency" and not item["passed"] for item in dep_preflight["items"]):
            failures.append("dependency_preflight: upstream without selected take did not fail")

        record("generation_service.submit_generation")
        first_take = ctx.generation_service.submit_generation(project_id, shots[0]["id"], compiled["id"], "draft")
        second_take = ctx.generation_service.submit_generation(project_id, shots[0]["id"], compiled["id"], "draft", parent_take_id=first_take["take_id"], changed_variable="seed")
        take_rows = ctx.db.get_conn().execute("SELECT prompt_version_id FROM v3_takes WHERE id IN (?, ?) ORDER BY id", (first_take["take_id"], second_take["take_id"])).fetchall()
        if take_rows[0]["prompt_version_id"] != take_rows[1]["prompt_version_id"]:
            failures.append("reroll_prompt: REROLL did not keep same prompt version")

        ctx.review_service.review_take({"take_id": first_take["take_id"], "verdict": "REROLL", "product_identity_score": 6, "mechanical_accuracy_score": 5, "material_accuracy_score": 7, "motion_realism_score": 6, "camera_execution_score": 7, "continuity_score": 7, "commercial_usability_score": 6, "safety": "pass", "error_codes_json": ["M001"], "reviewer_notes": "same issue", "next_action": "retry"})
        ctx.review_service.review_take({"take_id": second_take["take_id"], "verdict": "REROLL", "product_identity_score": 6, "mechanical_accuracy_score": 5, "material_accuracy_score": 7, "motion_realism_score": 6, "camera_execution_score": 7, "continuity_score": 7, "commercial_usability_score": 6, "safety": "pass", "error_codes_json": ["M001"], "reviewer_notes": "same issue", "next_action": "retry"})
        record("review_service.plan_retake")
        plan = ctx.review_service.plan_retake(second_take["take_id"])
        if plan["verdict"] != "REWRITE":
            failures.append("retake_rewrite: repeated M001 did not force REWRITE")

        before = len(ctx.generation_service.compile_prompt(project_id=project_id, shot_id=shots[0]["id"])["prompt_text"])
        ctx.shot_repository.update_shot(shots[0]["id"], {"subject_action": "Updated single action"})
        rewritten = ctx.generation_service.compile_prompt(project_id=project_id, shot_id=shots[0]["id"])
        if rewritten["version"] <= compiled["version"]:
            failures.append("rewrite_prompt: new prompt version not created")

    return {"total": len(cases), "failures": failures, "called_functions": sorted(set(CALLED_FUNCTIONS))}


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "cases" / "director_cases.json"
    result = run(path)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
