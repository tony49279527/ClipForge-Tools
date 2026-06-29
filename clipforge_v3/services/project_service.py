from __future__ import annotations

import json

from clipforge_v3.migrations import ensure_v3_schema, run_v3_migrations
from clipforge_v3.repositories import project_repository, take_repository
from clipforge_v3.schemas.product_truth import ProductTruthInput
from clipforge_v3.schemas.project import V3ProjectCreate, V3ProjectStatus
from clipforge_v3.services.assembly_service import rebuild_final_video
from clipforge_v3.services import asset_service
from clipforge_v3.services.continuity_service import list_continuity_states
from clipforge_v3.services.cost_service import build_cost_center
from clipforge_v3.services.observability_service import list_recent_events
from clipforge_v3.services.product_truth_service import (
    bootstrap_product_truth,
    get_latest_product_truth,
    list_product_truth_versions,
    save_product_truth,
)
from clipforge_v3.services.review_service import list_reviews
from clipforge_v3.services.scheduling_service import compute_schedule_state
from clipforge_v3.services.shot_service import confirm_all_shots, list_prompt_versions, list_shots, regenerate_director_plan
from clipforge_v3.services.take_service import list_takes

WORKFLOW_STEPS = [
    ("project_brief", "Brief"),
    ("product_truth", "Product Truth"),
    ("reference_assets", "Assets"),
    ("director_plan", "Director Plan"),
    ("shot_contracts", "Shot Contracts"),
    ("prompt_compilation", "Prompt Compile"),
    ("draft_generation", "Draft Generation"),
    ("production_generation", "Production Generation"),
    ("review", "Review"),
    ("final_assembly", "Final Assembly"),
    ("publish", "Publish"),
]
STEP_LABELS_ZH = {
    "project_brief": "项目简报",
    "product_truth": "产品事实",
    "reference_assets": "参考素材",
    "director_plan": "导演计划",
    "shot_contracts": "镜头合同",
    "prompt_compilation": "Prompt 编译",
    "draft_generation": "Draft 生成",
    "production_generation": "Production 生成",
    "review": "审核",
    "final_assembly": "最终拼接",
    "publish": "发布",
}


def create_project(project_input: V3ProjectCreate) -> dict:
    ensure_v3_schema()
    project_id = project_repository.create_project(
        {
            "project_name": project_input.project_name,
            "product_name": project_input.product_name,
            "product_category": project_input.product_category,
            "target_market": project_input.target_market,
            "target_audience": project_input.target_audience,
            "target_platform": project_input.target_platform,
            "aspect_ratio": project_input.aspect_ratio,
            "total_duration": project_input.total_duration,
            "default_clip_duration": project_input.default_clip_duration,
            "resolution": project_input.resolution,
            "language": project_input.language,
            "project_status": "draft",
            "current_stage": "product_truth",
            "product_url": project_input.product_url,
            "dimensions_input": project_input.dimensions_input,
            "materials_input": project_input.materials_input,
            "package_quantity": project_input.package_quantity,
            "parts_summary": project_input.parts_summary or project_input.source_description,
            "installation_method": project_input.installation_method,
            "working_surface_input": project_input.working_surface_input,
            "intended_for": project_input.intended_for,
            "not_for": project_input.not_for,
            "safety_notes": project_input.safety_notes,
            "director_plan_status": "not_started",
        }
    )
    project = dict(project_repository.get_project(project_id))
    bootstrap_product_truth(project=project)
    project_repository.update_project(
        project_id,
        {
            "max_draft_takes": 5,
            "max_production_takes": 3,
            "max_cost_cny": 300,
            "max_generation_seconds": 180,
            "good_enough_definition": "identity>=8 and mechanical>=8 and safety=pass",
            "final_assembly_valid": 0,
        },
    )
    project_repository.create_usage_event(
        {
            "project_id": project_id,
            "stage": "project_intake",
            "provider": "clipforge_v3",
            "model": "structured_rule_engine_v2",
            "duration": project_input.total_duration,
            "resolution": project_input.resolution,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "estimated_cost": 0,
            "status": "succeeded",
            "raw_usage_json": {"product_truth_bootstrapped": True},
        }
    )
    project_repository.create_operation_event(
        {
            "request_id": "system",
            "project_id": project_id,
            "stage": "project_create",
            "status": "succeeded",
            "message": "V3 project created.",
        }
    )
    return get_project_detail(project_id)


def list_projects() -> list[dict]:
    ensure_v3_schema()
    return [dict(row) for row in project_repository.list_projects()]


def update_product_truth(project_id: int, form_data: dict, approve: bool = False) -> dict:
    project = dict(project_repository.get_project(project_id))
    truth_input = ProductTruthInput(
        product_name=project["product_name"],
        product_category=project["product_category"],
        source_description=form_data.get("source_description") or project["parts_summary"],
        product_url=form_data.get("product_url") or project.get("product_url", ""),
        dimensions=form_data.get("dimensions_input") or project.get("dimensions_input", ""),
        materials=form_data.get("materials_input") or project.get("materials_input", ""),
        package_quantity=form_data.get("package_quantity") or project.get("package_quantity", ""),
        parts_summary=form_data.get("parts_summary") or project.get("parts_summary", ""),
        installation_method=form_data.get("installation_method") or project.get("installation_method", ""),
        working_surface=form_data.get("working_surface_input") or project.get("working_surface_input", ""),
        intended_for=form_data.get("intended_for") or project.get("intended_for", ""),
        not_for=form_data.get("not_for") or project.get("not_for", ""),
        safety_notes=form_data.get("safety_notes") or project.get("safety_notes", ""),
    )
    project_repository.update_project(
        project_id,
        {
            "product_url": truth_input.product_url,
            "dimensions_input": truth_input.dimensions,
            "materials_input": truth_input.materials,
            "package_quantity": truth_input.package_quantity,
            "parts_summary": truth_input.parts_summary,
            "installation_method": truth_input.installation_method,
            "working_surface_input": truth_input.working_surface,
            "intended_for": truth_input.intended_for,
            "not_for": truth_input.not_for,
            "safety_notes": truth_input.safety_notes,
        },
    )
    save_product_truth(project_id=project_id, product_input=truth_input, approve=approve, invalidate_downstream=True)
    if approve:
        project_repository.update_project(project_id, {"project_status": "product_truth_confirmed", "current_stage": "reference_assets"})
    return get_project_detail(project_id)


def generate_director_plan(project_id: int) -> dict:
    project = dict(project_repository.get_project(project_id))
    product_truth = get_latest_product_truth(project_id)
    if not product_truth or not product_truth["user_approved"]:
        raise ValueError("Product Truth must be confirmed before director planning.")
    assets = asset_service.list_assets(project_id)
    shots = regenerate_director_plan(project=project, product_truth=product_truth, assets=assets)
    project_repository.update_project(project_id, {"project_status": "director_plan_ready", "current_stage": "shot_contracts"})
    return {"project_id": project_id, "shot_count": len(shots)}


def get_project_detail(project_id: int) -> dict:
    ensure_v3_schema()
    project_row = project_repository.get_project(project_id)
    if not project_row:
        raise KeyError(f"Project {project_id} not found")
    project = dict(project_row)
    truth = get_latest_product_truth(project_id)
    truth_versions = list_product_truth_versions(project_id)
    assets = asset_service.list_assets(project_id)
    shots = list_shots(project_id)
    prompt_versions = list_prompt_versions(project_id)
    takes = list_takes(project_id)
    generation_submissions = take_repository.list_generation_submissions(project_id)
    preflights = []
    for row in project_repository.list_preflight_checks(project_id):
        payload = dict(row)
        payload["result_json"] = json.loads(payload["result_json"] or "{}")
        payload["allow_submit"] = bool(payload["allow_submit"])
        preflights.append(payload)
    usage_events = [dict(row) for row in project_repository.list_usage_events(project_id)]
    reviews = list_reviews(project_id)
    continuity_states = list_continuity_states(project_id)
    retake_plans = []
    for row in project_repository.list_retake_plans(project_id):
        payload = dict(row)
        payload["result_json"] = json.loads(payload["result_json"] or "{}")
        retake_plans.append(payload)
    final_assemblies = []
    for row in project_repository.list_final_assemblies(project_id):
        payload = dict(row)
        payload["assembly_take_ids_json"] = json.loads(payload["assembly_take_ids_json"] or "[]")
        payload["invalidated"] = bool(payload["invalidated"])
        final_assemblies.append(payload)
    selected_business_shot_ids = {shot["shot_id"] for shot in shots if shot.get("selected_take_id")}
    failed_business_shot_ids = {shot["shot_id"] for shot in shots if shot.get("status") == "failed"}
    schedule = compute_schedule_state(shots, selected_business_shot_ids, failed_business_shot_ids)
    metrics = project_repository.get_project_metrics(project_id)
    blocking_errors = collect_blocking_errors(preflights, shots)
    cost_center = build_cost_center(project=project, shots=shots, takes=takes, usage_events=usage_events)
    recent_activity = list_recent_events(project_id, limit=12)
    steps = build_step_frames(project["current_stage"], metrics, blocking_errors, shots, preflights, final_assemblies)
    return {
        "project": project,
        "product_truth": truth,
        "product_truth_versions": truth_versions,
        "assets": assets,
        "shots": shots,
        "prompt_versions": prompt_versions,
        "takes": takes,
        "generation_submissions": generation_submissions,
        "preflights": preflights,
        "reviews": reviews,
        "usage_events": usage_events,
        "retake_plans": retake_plans,
        "continuity_states": continuity_states,
        "schedule": schedule,
        "final_assemblies": final_assemblies,
        "blocking_errors": blocking_errors,
        "cost_center": cost_center,
        "recent_activity": recent_activity,
        "metrics": metrics,
        "steps": steps,
    }


def get_project_status(project_id: int) -> V3ProjectStatus:
    detail = get_project_detail(project_id)
    project = detail["project"]
    metrics = detail["metrics"]
    truth = detail["product_truth"]
    return V3ProjectStatus(
        project_id=project["id"],
        project_status=project["project_status"],
        current_stage=project["current_stage"],
        step_counts={key: metrics.get(key, 0) for key, _label in WORKFLOW_STEPS},
        latest_prompt_version_count=metrics["prompt_versions"],
        latest_take_count=metrics["takes"],
        latest_review_count=metrics["review"],
        latest_continuity_state_count=metrics["continuity_states"],
        product_truth_version=truth["version"] if truth else None,
    )


def confirm_shot_contracts(project_id: int) -> None:
    confirm_all_shots(project_id)
    project_repository.update_project(project_id, {"project_status": "shot_contracts_confirmed"})


def run_migrations() -> list[str]:
    return run_v3_migrations()


def build_step_frames(current_stage: str, metrics: dict[str, int], blocking_errors: list[dict] | None = None, shots: list[dict] | None = None, preflights: list[dict] | None = None, final_assemblies: list[dict] | None = None) -> list[dict]:
    order = [step for step, _ in WORKFLOW_STEPS]
    blocking_errors = blocking_errors or []
    shots = shots or []
    preflights = preflights or []
    final_assemblies = final_assemblies or []
    current_stage = current_stage if current_stage in order else "project_brief"
    steps = []
    for step, label in WORKFLOW_STEPS:
        if step == current_stage:
            state = "in_progress"
        elif order.index(step) < order.index(current_stage):
            state = "approved"
        else:
            state = "not_started"
        reason = ""
        if step in {"prompt_compilation", "draft_generation", "production_generation"} and blocking_errors:
            state = "blocked"
            reason = "Blocking errors require operator action."
        if step == "shot_contracts" and any(shot.get("status") == "invalidated" for shot in shots):
            state = "invalidated"
            reason = "Product Truth or shot order changed downstream contracts."
        if step == "draft_generation" and preflights and any(not item.get("allow_submit") for item in preflights):
            state = "warning"
            reason = "Latest preflight includes failed checks."
        if step == "final_assembly" and final_assemblies and final_assemblies[0].get("invalidated"):
            state = "invalidated"
            reason = "Selected take changed after assembly."
        steps.append({"key": step, "label": label, "label_zh": STEP_LABELS_ZH.get(step, label), "state": state, "count": metrics.get(step, 0), "reason": reason})
    return steps


def collect_blocking_errors(preflights: list[dict], shots: list[dict]) -> list[dict]:
    errors = []
    for shot in shots:
        if shot.get("status") in {"blocked_missing_assets", "invalidated", "failed"}:
            errors.append({"scope": shot["shot_id"], "code": shot.get("status"), "message": "处理方式：补齐素材、重新确认 Product Truth，或重新生成 Director Plan。"})
    for preflight in preflights[:5]:
        result = preflight.get("result_json", {})
        for item in result.get("items", []):
            if item.get("severity") == "blocking_error" and not item.get("passed"):
                errors.append({"scope": f"shot:{preflight.get('shot_id')}", "code": item.get("name"), "message": f"{item.get('message')} 下一步：修复该检查项后重新 Preflight。"})
    return errors
