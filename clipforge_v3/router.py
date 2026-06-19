from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from clipforge_v3 import is_v3_enabled
from clipforge_v3.repositories import shot_repository, take_repository
from clipforge_v3.schemas.project import V3ProjectCreate
from clipforge_v3.services import asset_service, project_service, shot_service
from clipforge_v3.services.assembly_service import rebuild_final_video
from clipforge_v3.services.generation_service import build_paid_confirmation, compile_prompt, get_video_provider_mode, lock_prompt, preflight, real_api_enabled, submit_generation
from clipforge_v3.services.observability_service import log_event, new_request_id
from clipforge_v3.services.product_truth_service import confirm_latest_product_truth
from clipforge_v3.services.readiness_service import readiness
from clipforge_v3.services.review_service import plan_retake, review_take
from clipforge_v3.services.storage_service import StorageError, get_storage
from clipforge_v3.services.take_service import clear_selected_take, compare_takes, mark_local_file_deleted, restore_take_history, select_take
from db import DB_PATH, DB_URL

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
router = APIRouter(prefix="/v3", tags=["clipforge-v3"])
OUTPUTS_DIR = Path(os.getenv("OUTPUTS_DIR", "outputs")).resolve()

DEMO_PRODUCT = {
    "project_name": "Buffing Wheel Demo Project",
    "product_name": "6 inch stitched cotton buffing wheel",
    "product_category": "hardware polishing tool",
    "target_market": "Amazon US",
    "target_audience": "Amazon tool buyers and garage DIY users",
    "target_platform": "amazon",
    "aspect_ratio": "16:9",
    "total_duration": 20,
    "default_clip_duration": 5,
    "resolution": "720p",
    "language": "en",
    "materials_input": "natural off-white stitched cotton",
    "dimensions_input": "6-inch diameter, 1-inch thickness, 1/2-inch arbor hole",
    "working_surface_input": "outer cotton circumference for final polishing",
    "source_description": (
        "6 inch stitched cotton buffing wheel for final polishing. Approximately 70 ply, "
        "1 inch thickness, 1/2-inch arbor hole, concentric stitched rings, natural off-white cotton, "
        "mounted by passing the spindle through the center hole and securing with washer and nut. "
        "Use the outer cotton circumference as the working surface. Do not turn into wool, felt, "
        "synthetic fur, or grinding stone."
    ),
}


def require_v3_enabled() -> None:
    if not is_v3_enabled():
        raise HTTPException(status_code=404, detail="ClipForge 3.0 is disabled")


def build_context(request: Request) -> dict:
    lang = request.query_params.get("lang", "zh")
    is_zh = lang != "en"
    return {
        "request": request,
        "v3_enabled": is_v3_enabled(),
        "clipforge_v3_enabled": is_v3_enabled(),
        "is_zh": is_zh,
        "lang": "zh" if is_zh else "en",
        "lang_switch_en": str(request.url.include_query_params(lang="en")),
        "lang_switch_zh": str(request.url.include_query_params(lang="zh")),
        "app_version": os.getenv("APP_VERSION", "v3"),
        "v3_video_provider": get_video_provider_mode(),
        "v3_real_api_enabled": real_api_enabled(),
    }


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, "true" if default else "false").strip().lower() in {"1", "true", "yes", "on"}


def _maintenance_mode_enabled() -> bool:
    for name in ("V3_MAINTENANCE_MODE", "CLIPFORGE_MAINTENANCE_MODE", "MAINTENANCE_MODE"):
        if _env_bool(name):
            return True
    return False


def _writes_enabled() -> bool:
    for name in ("V3_WRITE_FREEZE", "CLIPFORGE_WRITE_FREEZE", "WRITE_FREEZE"):
        if _env_bool(name):
            return False
    if os.getenv("V3_WRITES_ENABLED"):
        return _env_bool("V3_WRITES_ENABLED", True)
    return not _maintenance_mode_enabled()


def _database_backend_label() -> str:
    configured_url = os.getenv("DATABASE_URL", "").strip() or os.getenv("DB_URL", DB_URL).strip()
    if configured_url:
        lowered = configured_url.lower()
        if "postgres" in lowered:
            return "PostgreSQL"
        if lowered.startswith("sqlite"):
            return "SQLite"
        return "Custom database URL"
    return f"SQLite file ({DB_PATH.name})"


def _system_overview() -> dict:
    ready = readiness()
    checks = ready.get("checks", {})
    return {
        "provider_mode": get_video_provider_mode(),
        "paid_disabled": not real_api_enabled(),
        "database_backend": _database_backend_label(),
        "storage_backend": os.getenv("V3_STORAGE_BACKEND", os.getenv("STORAGE_BACKEND", "local")).strip().lower() or "local",
        "redis_ok": checks.get("redis", {}).get("ok", False),
        "redis_message": checks.get("redis", {}).get("message", "Redis status unavailable."),
        "worker_ok": checks.get("worker", {}).get("ok", False),
        "worker_message": checks.get("worker", {}).get("message", "Worker status unavailable."),
        "maintenance_mode": _maintenance_mode_enabled(),
        "writes_enabled": _writes_enabled(),
        "ready_checks": ready,
    }


def _demo_redirect(project_id: int | None = None, notice: str | None = None, error: str | None = None) -> RedirectResponse:
    params: dict[str, str] = {}
    if project_id is not None:
        params["project_id"] = str(project_id)
    if notice:
        params["notice"] = notice
    if error:
        params["error"] = error
    suffix = f"?{urlencode(params)}" if params else ""
    return RedirectResponse(url=f"/v3{suffix}", status_code=303)


def _take_preview_url(take: dict) -> str | None:
    if take.get("local_path"):
        return f"/v3/takes/{take['id']}/video"
    if take.get("remote_url"):
        return take["remote_url"]
    return None


def _demo_can_override_prompt(prompt_version: dict) -> bool:
    issues = prompt_version.get("validation_result_json", {}).get("lint_issues", [])
    blocking = [item for item in issues if item.get("severity") == "blocking_error"]
    return bool(blocking) and all(item.get("code") == "MISSING_REQUIRED_REFERENCE" for item in blocking)


@router.get("/health")
def v3_health():
    require_v3_enabled()
    applied = project_service.run_migrations()
    return {"ok": True, "feature": "clipforge-v3", "version": os.getenv("APP_VERSION", "v3"), "applied_migrations": applied}


@router.get("/ready")
def v3_ready():
    require_v3_enabled()
    return readiness()


@router.get("/storage/local/{project_id}/{filename}")
def v3_local_storage_file(project_id: int, filename: str):
    require_v3_enabled()
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid file path")
    storage = get_storage()
    if getattr(storage, "backend", "local") != "local":
        raise HTTPException(status_code=404, detail="Local storage is not active")
    path = Path(storage.base_dir) / str(project_id) / filename
    try:
        path.resolve().relative_to((Path(storage.base_dir) / str(project_id)).resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid file path") from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path)


@router.get("/takes/{take_id}/video")
def v3_take_video(take_id: int):
    require_v3_enabled()
    row = take_repository.get_take(take_id)
    if not row:
        raise HTTPException(status_code=404, detail="Take not found")
    local_path = row["local_path"]
    if not local_path:
        raise HTTPException(status_code=404, detail="This take does not have a local preview video.")
    path = Path(local_path).resolve()
    try:
        path.relative_to(OUTPUTS_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid take preview path") from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Preview file not found")
    return FileResponse(path, media_type="video/mp4")


@router.get("")
def v3_index(request: Request):
    require_v3_enabled()
    context = build_context(request)
    projects = project_service.list_projects()
    selected_project_id = request.query_params.get("project_id")
    selected_detail = None
    latest_prompt = None
    latest_take = None
    if selected_project_id:
        try:
            selected_detail = project_service.get_project_detail(int(selected_project_id))
        except Exception:
            selected_detail = None
    if selected_detail:
        if selected_detail["prompt_versions"]:
            latest_prompt = max(selected_detail["prompt_versions"], key=lambda item: item["id"])
        if selected_detail["takes"]:
            latest_take = max(selected_detail["takes"], key=lambda item: item["id"])
            latest_take["preview_url"] = _take_preview_url(latest_take)
    context.update(
        {
            "projects": projects[:10],
            "all_projects": projects,
            "selected_detail": selected_detail,
            "selected_project": selected_detail["project"] if selected_detail else None,
            "latest_prompt": latest_prompt,
            "latest_take": latest_take,
            "notice": request.query_params.get("notice", ""),
            "error_message": request.query_params.get("error", ""),
            "demo_defaults": DEMO_PRODUCT,
            "system_overview": _system_overview(),
        }
    )
    return templates.TemplateResponse("v3/index.html", context)


@router.post("/demo/select-project")
def v3_demo_select_project(project_id: int = Form(...)):
    require_v3_enabled()
    return _demo_redirect(project_id=project_id)


@router.post("/demo/projects")
def v3_demo_create_project(
    project_name: str = Form(""),
    product_name: str = Form(""),
    product_category: str = Form(""),
    material: str = Form(""),
    key_selling_points: str = Form(""),
    target_marketplace: str = Form(""),
    video_style: str = Form(""),
    use_demo_product: bool = Form(False),
):
    require_v3_enabled()
    defaults = DEMO_PRODUCT if use_demo_product or not product_name.strip() else {}
    resolved_project_name = project_name.strip() or defaults.get("project_name") or f"{product_name.strip() or 'ClipForge'} Demo"
    resolved_product_name = product_name.strip() or defaults.get("product_name", "Demo Product")
    resolved_category = product_category.strip() or defaults.get("product_category", "hardware tool")
    resolved_market = target_marketplace.strip() or defaults.get("target_market", "Amazon US")
    source_description = defaults.get("source_description", "")
    if not source_description:
        source_description = (
            f"{resolved_product_name}. Materials: {material.strip() or 'not provided'}. "
            f"Key selling points: {key_selling_points.strip() or 'not provided'}. "
            f"Video style: {video_style.strip() or 'clean Amazon demo'}. "
            "Show product identity, safe use, installation relationship, and one clear action per shot."
        )
    payload = V3ProjectCreate(
        project_name=resolved_project_name,
        product_name=resolved_product_name,
        product_category=resolved_category,
        target_market=resolved_market,
        target_audience="Operators validating a mock V3 demo flow",
        target_platform=defaults.get("target_platform", "amazon"),
        aspect_ratio=defaults.get("aspect_ratio", "16:9"),
        total_duration=defaults.get("total_duration", 20),
        default_clip_duration=defaults.get("default_clip_duration", 5),
        resolution=defaults.get("resolution", "720p"),
        language=defaults.get("language", "en"),
        source_description=source_description,
        dimensions_input=defaults.get("dimensions_input", ""),
        materials_input=material.strip() or defaults.get("materials_input", ""),
        working_surface_input=defaults.get("working_surface_input", ""),
        parts_summary=key_selling_points.strip() or source_description,
        installation_method="Mount securely, then show one safe polishing action.",
        intended_for="Mock V3 demo flow",
        safety_notes="Demo mode only. Do not treat this mock output as a real safety instruction video.",
    )
    project = project_service.create_project(payload)
    return _demo_redirect(project_id=project["project"]["id"], notice="Demo project created.")


@router.post("/demo/projects/{project_id}/product-info")
def v3_demo_save_product_info(
    project_id: int,
    source_description: str = Form(...),
    dimensions_input: str = Form(""),
    materials_input: str = Form(""),
    working_surface_input: str = Form(""),
    parts_summary: str = Form(""),
    target_marketplace: str = Form(""),
    video_style: str = Form(""),
    confirm_truth: bool = Form(False),
):
    require_v3_enabled()
    project_service.update_product_truth(
        project_id,
        {
            "source_description": source_description,
            "dimensions_input": dimensions_input,
            "materials_input": materials_input,
            "working_surface_input": working_surface_input,
            "parts_summary": parts_summary or source_description,
            "product_url": "",
            "package_quantity": "",
            "installation_method": f"Marketplace: {target_marketplace}. Style: {video_style}.",
            "intended_for": "Mock product video validation",
            "not_for": "Paid generation",
            "safety_notes": "Demo mode only.",
        },
        approve=confirm_truth,
    )
    message = "Product Truth saved and confirmed." if confirm_truth else "Product info saved."
    return _demo_redirect(project_id=project_id, notice=message)


@router.post("/demo/projects/{project_id}/assets/upload")
def v3_demo_upload_asset(
    project_id: int,
    asset_file: UploadFile = File(...),
):
    require_v3_enabled()
    try:
        stored = get_storage().save_upload(project_id=project_id, upload=asset_file)
        asset_service.create_asset(
            project_id=project_id,
            file_path=Path(stored["local_path"]),
            original_filename=stored["original_filename"],
            primary_role="product_identity",
            secondary_role=None,
            must_transfer=["overall geometry", "center hole", "material color"],
            must_not_transfer=["background", "text overlay", "camera move"],
            applies_to_shots=["S01", "S02", "S03"],
            is_identity_anchor=True,
            user_approved=True,
            mime_type=stored["mime_type"],
            storage_backend=stored["backend"],
            access_url=stored["access_url"],
            object_key=stored.get("object_key"),
            content_type=stored.get("content_type"),
            size_bytes=stored.get("size_bytes"),
        )
    except StorageError as exc:
        return _demo_redirect(project_id=project_id, error=f"{exc} Please upload a JPG, PNG, or WEBP image.")
    return _demo_redirect(project_id=project_id, notice="Product image uploaded for mock planning.")


@router.post("/demo/projects/{project_id}/assets/sample")
def v3_demo_sample_asset(project_id: int):
    require_v3_enabled()
    asset_service.create_demo_placeholder_asset(project_id=project_id)
    return _demo_redirect(project_id=project_id, notice="Built-in demo image is ready.")


@router.post("/demo/projects/{project_id}/prompt")
def v3_demo_generate_prompt(project_id: int):
    require_v3_enabled()
    detail = project_service.get_project_detail(project_id)
    if not detail["product_truth"] or not detail["product_truth"]["user_approved"]:
        return _demo_redirect(project_id=project_id, error="Please confirm Product Truth before generating a mock prompt.")
    if not any(asset["primary_role"] == "product_identity" and asset["user_approved"] for asset in detail["assets"]):
        return _demo_redirect(project_id=project_id, error="Please upload a product image or use the built-in demo image first.")
    if not detail["shots"]:
        project_service.generate_director_plan(project_id)
        detail = project_service.get_project_detail(project_id)
    if any(not shot.get("user_approved") for shot in detail["shots"]):
        project_service.confirm_shot_contracts(project_id)
        detail = project_service.get_project_detail(project_id)
    first_shot = sorted(detail["shots"], key=lambda item: item["sequence_index"])[0]
    prompt_version = compile_prompt(project_id=project_id, shot_id=first_shot["id"])
    if get_video_provider_mode() == "mock" and not prompt_version.get("allow_submit") and _demo_can_override_prompt(prompt_version):
        validation = dict(prompt_version.get("validation_result_json") or {})
        validation["demo_override"] = {
            "enabled": True,
            "reason": "Mock-only demo flow bypassed a non-paid identity-preservation blocker.",
        }
        warnings = list(prompt_version.get("compiler_warnings_json") or [])
        warnings.append("demo_override:mock_generation_allowed")
        shot_repository.update_prompt_version(
            prompt_version["id"],
            {
                "allow_submit": 1,
                "validation_result_json": validation,
                "compiler_warnings_json": warnings,
            },
        )
    return _demo_redirect(project_id=project_id, notice=f"Mock prompt prepared for {first_shot['shot_id']}.")


@router.post("/demo/projects/{project_id}/generate")
def v3_demo_generate_mock(project_id: int):
    require_v3_enabled()
    if get_video_provider_mode() != "mock" or real_api_enabled():
        return _demo_redirect(project_id=project_id, error="Demo flow is locked to mock mode only. Real paid generation is disabled here.")
    detail = project_service.get_project_detail(project_id)
    if not detail["shots"]:
        return _demo_redirect(project_id=project_id, error="Generate a mock prompt first.")
    prompt_versions = sorted(detail["prompt_versions"], key=lambda item: item["id"], reverse=True)
    if not prompt_versions:
        return _demo_redirect(project_id=project_id, error="No prompt is available yet. Generate a mock prompt first.")
    first_shot = sorted(detail["shots"], key=lambda item: item["sequence_index"])[0]
    prompt_version = next((item for item in prompt_versions if item["shot_id"] == first_shot["id"]), None)
    if not prompt_version:
        return _demo_redirect(project_id=project_id, error="No prompt is available for the first shot yet.")
    result = submit_generation(project_id, first_shot["id"], prompt_version["id"], "draft")
    log_event(
        stage="demo_mock_generate",
        status="succeeded",
        request_id=new_request_id(),
        project_id=project_id,
        shot_id=first_shot["id"],
        take_id=result.get("take_id"),
        message="Demo mock generation finished without calling a paid provider.",
    )
    return _demo_redirect(project_id=project_id, notice=f"Mock take created for {first_shot['shot_id']}. Cost remains 0.")


@router.get("/projects")
def v3_projects(request: Request):
    require_v3_enabled()
    context = build_context(request)
    context.update({"projects": project_service.list_projects()})
    return templates.TemplateResponse("v3/projects.html", context)


@router.get("/projects/new")
def v3_new_project(request: Request):
    require_v3_enabled()
    context = build_context(request)
    context.update(
        {
            "defaults": {
                "target_market": "US",
                "target_platform": "amazon",
                "aspect_ratio": "16:9",
                "total_duration": 30,
                "default_clip_duration": 6,
                "resolution": "1080p",
                "language": "en",
            }
        }
    )
    return templates.TemplateResponse("v3/project_new.html", context)


@router.post("/projects")
def v3_create_project(
    request: Request,
    project_name: str = Form(...),
    product_name: str = Form(...),
    product_category: str = Form(...),
    target_market: str = Form(...),
    target_audience: str = Form(...),
    target_platform: str = Form(...),
    aspect_ratio: str = Form(...),
    total_duration: int = Form(...),
    default_clip_duration: int = Form(...),
    resolution: str = Form(...),
    language: str = Form(...),
    source_description: str = Form(...),
    product_url: str = Form(""),
    dimensions_input: str = Form(""),
    materials_input: str = Form(""),
    package_quantity: str = Form(""),
    parts_summary: str = Form(""),
    installation_method: str = Form(""),
    working_surface_input: str = Form(""),
    intended_for: str = Form(""),
    not_for: str = Form(""),
    safety_notes: str = Form(""),
):
    require_v3_enabled()
    try:
        payload = V3ProjectCreate(
            project_name=project_name,
            product_name=product_name,
            product_category=product_category,
            target_market=target_market,
            target_audience=target_audience,
            target_platform=target_platform,
            aspect_ratio=aspect_ratio,
            total_duration=total_duration,
            default_clip_duration=default_clip_duration,
            resolution=resolution,
            language=language,
            source_description=source_description,
            product_url=product_url,
            dimensions_input=dimensions_input,
            materials_input=materials_input,
            package_quantity=package_quantity,
            parts_summary=parts_summary,
            installation_method=installation_method,
            working_surface_input=working_surface_input,
            intended_for=intended_for,
            not_for=not_for,
            safety_notes=safety_notes,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    project = project_service.create_project(payload)
    log_event(stage="project_create", status="succeeded", request_id=new_request_id(), project_id=project["project"]["id"], message="Project created from V3 form.")
    return RedirectResponse(url=f"/v3/projects/{project['project']['id']}", status_code=303)


@router.get("/projects/{project_id}")
def v3_project_detail(request: Request, project_id: int):
    require_v3_enabled()
    try:
        detail = project_service.get_project_detail(project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    context = build_context(request)
    context.update(detail)
    return templates.TemplateResponse("v3/project_detail.html", context)


@router.post("/projects/{project_id}/product-truth/save")
def v3_save_product_truth(
    project_id: int,
    source_description: str = Form(...),
    product_url: str = Form(""),
    dimensions_input: str = Form(""),
    materials_input: str = Form(""),
    package_quantity: str = Form(""),
    parts_summary: str = Form(""),
    installation_method: str = Form(""),
    working_surface_input: str = Form(""),
    intended_for: str = Form(""),
    not_for: str = Form(""),
    safety_notes: str = Form(""),
):
    require_v3_enabled()
    project_service.update_product_truth(
        project_id,
        {
            "source_description": source_description,
            "product_url": product_url,
            "dimensions_input": dimensions_input,
            "materials_input": materials_input,
            "package_quantity": package_quantity,
            "parts_summary": parts_summary,
            "installation_method": installation_method,
            "working_surface_input": working_surface_input,
            "intended_for": intended_for,
            "not_for": not_for,
            "safety_notes": safety_notes,
        },
        approve=False,
    )
    log_event(stage="product_truth_save", status="succeeded", project_id=project_id, message="Product Truth saved; downstream contracts may be invalidated.")
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/product-truth/confirm")
def v3_confirm_product_truth(project_id: int):
    require_v3_enabled()
    confirm_latest_product_truth(project_id)
    log_event(stage="product_truth_confirm", status="succeeded", project_id=project_id, message="Product Truth confirmed by operator.")
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/assets")
def v3_upload_asset(
    project_id: int,
    primary_role: str = Form(...),
    secondary_role: str = Form(""),
    must_transfer: str = Form(""),
    must_not_transfer: str = Form(""),
    applies_to_shots: str = Form(""),
    is_identity_anchor: bool = Form(False),
    user_approved: bool = Form(False),
    asset_file: UploadFile = File(...),
):
    require_v3_enabled()
    try:
        stored = get_storage().save_upload(project_id=project_id, upload=asset_file)
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=f"{exc} Next step: upload a supported file or reduce the file size.") from exc
    asset_service.create_asset(
        project_id=project_id,
        file_path=Path(stored["local_path"]),
        original_filename=stored["original_filename"],
        primary_role=primary_role,
        secondary_role=secondary_role or None,
        must_transfer=[item.strip() for item in must_transfer.split(",") if item.strip()],
        must_not_transfer=[item.strip() for item in must_not_transfer.split(",") if item.strip()],
        applies_to_shots=[item.strip() for item in applies_to_shots.split(",") if item.strip()],
        is_identity_anchor=is_identity_anchor,
        user_approved=user_approved,
        mime_type=stored["mime_type"],
        storage_backend=stored["backend"],
        access_url=stored["access_url"],
        object_key=stored.get("object_key"),
        content_type=stored.get("content_type"),
        size_bytes=stored.get("size_bytes"),
    )
    log_event(stage="asset_upload", status="succeeded", project_id=project_id, message=f"Asset uploaded with role {primary_role}.")
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/assets/{asset_id}/delete")
def v3_delete_asset(project_id: int, asset_id: int):
    require_v3_enabled()
    asset_service.delete_asset(asset_id)
    log_event(stage="asset_delete", status="succeeded", project_id=project_id, message=f"Asset {asset_id} soft deleted.")
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/assets/{asset_id}/replace")
def v3_replace_asset(
    project_id: int,
    asset_id: int,
    primary_role: str = Form(...),
    secondary_role: str = Form(""),
    must_transfer: str = Form(""),
    must_not_transfer: str = Form(""),
    applies_to_shots: str = Form(""),
    is_identity_anchor: bool = Form(False),
    user_approved: bool = Form(False),
    asset_file: UploadFile = File(...),
):
    require_v3_enabled()
    try:
        stored = get_storage().save_upload(project_id=project_id, upload=asset_file)
    except StorageError as exc:
        raise HTTPException(status_code=400, detail=f"{exc} Next step: upload a supported replacement file.") from exc
    asset_service.replace_asset(
        old_asset_id=asset_id,
        project_id=project_id,
        file_path=Path(stored["local_path"]),
        original_filename=stored["original_filename"],
        primary_role=primary_role,
        secondary_role=secondary_role or None,
        must_transfer=[item.strip() for item in must_transfer.split(",") if item.strip()],
        must_not_transfer=[item.strip() for item in must_not_transfer.split(",") if item.strip()],
        applies_to_shots=[item.strip() for item in applies_to_shots.split(",") if item.strip()],
        is_identity_anchor=is_identity_anchor,
        user_approved=user_approved,
        mime_type=stored["mime_type"],
        storage_backend=stored["backend"],
        access_url=stored["access_url"],
        object_key=stored.get("object_key"),
        content_type=stored.get("content_type"),
        size_bytes=stored.get("size_bytes"),
    )
    log_event(stage="asset_replace", status="succeeded", project_id=project_id, message=f"Asset {asset_id} replaced with history retained.")
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/director-plan/generate")
def v3_generate_director_plan(project_id: int):
    require_v3_enabled()
    try:
        project_service.generate_director_plan(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_event(stage="director_plan", status="succeeded", project_id=project_id, message="Director plan generated.")
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/shots/confirm-all")
def v3_confirm_shots(project_id: int):
    require_v3_enabled()
    project_service.confirm_shot_contracts(project_id)
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/shots/add")
def v3_add_shot(project_id: int):
    require_v3_enabled()
    shot_service.create_manual_shot(project_id)
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/shots/{shot_id}/delete")
def v3_delete_shot(project_id: int, shot_id: int):
    require_v3_enabled()
    shot_service.delete_shot(shot_id, project_id)
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/shots/{shot_id}/copy")
def v3_copy_shot(project_id: int, shot_id: int):
    require_v3_enabled()
    shot_service.copy_shot(project_id, shot_id)
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/shots/{shot_id}/split")
def v3_split_shot(project_id: int, shot_id: int):
    require_v3_enabled()
    shot_service.split_shot(project_id, shot_id)
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/shots/{shot_id}/disable")
def v3_disable_shot(project_id: int, shot_id: int):
    require_v3_enabled()
    shot_service.disable_shot(shot_id)
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/shots/{shot_id}/move-{direction}")
def v3_move_shot(project_id: int, shot_id: int, direction: str):
    require_v3_enabled()
    if direction not in {"up", "down"}:
        raise HTTPException(status_code=400, detail="Invalid direction")
    shot_service.move_shot(project_id, shot_id, direction)
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/shots/{shot_id}/update")
def v3_update_shot(
    project_id: int,
    shot_id: int,
    purpose: str = Form(...),
    subject_action: str = Form(...),
    single_visible_beat: str = Form(...),
):
    require_v3_enabled()
    shot_service.update_shot_fields(
        shot_id,
        {
            "purpose": purpose,
            "subject_action": subject_action,
            "single_visible_beat": single_visible_beat,
        },
    )
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/shots/{shot_id}/compile")
def v3_compile_shot(project_id: int, shot_id: int):
    require_v3_enabled()
    compile_prompt(project_id=project_id, shot_id=shot_id)
    log_event(stage="prompt_compile", status="succeeded", project_id=project_id, shot_id=shot_id, message="Prompt compiled.")
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/prompts/{prompt_version_id}/lock")
def v3_lock_prompt(project_id: int, prompt_version_id: int):
    require_v3_enabled()
    lock_prompt(prompt_version_id)
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/shots/{shot_id}/preflight/{tier}")
def v3_preflight(project_id: int, shot_id: int, tier: str, prompt_version_id: int = Form(...)):
    require_v3_enabled()
    preflight(project_id, shot_id, prompt_version_id, tier)
    log_event(stage=f"preflight_{tier}", status="succeeded", project_id=project_id, shot_id=shot_id, message="Generation preflight completed.")
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/shots/{shot_id}/submit/{tier}")
def v3_submit_generation(project_id: int, shot_id: int, tier: str, prompt_version_id: int = Form(...)):
    require_v3_enabled()
    result = submit_generation(project_id, shot_id, prompt_version_id, tier)
    log_event(stage=f"submit_{tier}", status="succeeded", project_id=project_id, shot_id=shot_id, take_id=result.get("take_id"), message="Generation submitted.")
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/shots/{shot_id}/submit-real/{tier}")
def v3_submit_real_generation(
    project_id: int,
    shot_id: int,
    tier: str,
    prompt_version_id: int = Form(...),
    paid_confirmed: bool = Form(False),
    paid_confirmation_token: str = Form(""),
):
    require_v3_enabled()
    result = submit_generation(
        project_id,
        shot_id,
        prompt_version_id,
        tier,
        paid_confirmed=paid_confirmed,
        paid_confirmation_token=paid_confirmation_token,
    )
    log_event(stage=f"submit_real_{tier}", status="queued", project_id=project_id, shot_id=shot_id, take_id=result.get("take_id"), message="Real provider generation reserved/enqueued.")
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.get("/projects/{project_id}/shots/{shot_id}/paid-confirmation/{tier}")
def v3_paid_confirmation(project_id: int, shot_id: int, tier: str, prompt_version_id: int):
    require_v3_enabled()
    return build_paid_confirmation(project_id=project_id, shot_id=shot_id, prompt_version_id=prompt_version_id, tier=tier)


@router.post("/projects/{project_id}/takes/{take_id}/select")
def v3_select_take(project_id: int, take_id: int):
    require_v3_enabled()
    select_take(project_id, take_id)
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/shots/{shot_id}/clear-selected")
def v3_clear_selected_take(project_id: int, shot_id: int):
    require_v3_enabled()
    clear_selected_take(project_id, shot_id)
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/takes/{take_id}/delete-local")
def v3_delete_take_local(project_id: int, take_id: int):
    require_v3_enabled()
    mark_local_file_deleted(take_id)
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/takes/{take_id}/restore")
def v3_restore_take(project_id: int, take_id: int):
    require_v3_enabled()
    restore_take_history(project_id, take_id)
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/takes/{take_id}/review")
def v3_review_take(
    project_id: int,
    take_id: int,
    verdict: str = Form(...),
    product_identity_score: int = Form(...),
    mechanical_accuracy_score: int = Form(...),
    material_accuracy_score: int = Form(...),
    motion_realism_score: int = Form(...),
    camera_execution_score: int = Form(...),
    continuity_score: int = Form(...),
    commercial_usability_score: int = Form(...),
    safety: str = Form(...),
    error_codes_json: str = Form(""),
    reviewer_notes: str = Form(""),
    next_action: str = Form(...),
):
    require_v3_enabled()
    review_take(
        {
            "take_id": take_id,
            "verdict": verdict,
            "product_identity_score": product_identity_score,
            "mechanical_accuracy_score": mechanical_accuracy_score,
            "material_accuracy_score": material_accuracy_score,
            "motion_realism_score": motion_realism_score,
            "camera_execution_score": camera_execution_score,
            "continuity_score": continuity_score,
            "commercial_usability_score": commercial_usability_score,
            "safety": safety,
            "error_codes_json": [item.strip() for item in error_codes_json.split(",") if item.strip()],
            "reviewer_notes": reviewer_notes,
            "next_action": next_action,
        }
    )
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/takes/{take_id}/plan-retake")
def v3_plan_retake(project_id: int, take_id: int):
    require_v3_enabled()
    plan_retake(take_id)
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.get("/projects/{project_id}/takes/compare")
def v3_compare_takes(project_id: int, left_take_id: int, right_take_id: int):
    require_v3_enabled()
    return compare_takes(left_take_id, right_take_id)


@router.post("/projects/{project_id}/final-assembly/rebuild")
def v3_rebuild_final_video(project_id: int):
    require_v3_enabled()
    rebuild_final_video(project_id)
    return RedirectResponse(url=f"/v3/projects/{project_id}", status_code=303)


@router.get("/projects/{project_id}/publish-gate")
def v3_publish_gate(project_id: int):
    require_v3_enabled()
    detail = project_service.get_project_detail(project_id)
    selected = [shot for shot in detail["shots"] if shot.get("selected_take_id")]
    missing = [shot["shot_id"] for shot in detail["shots"] if not shot.get("selected_take_id") and shot.get("status") != "disabled"]
    latest_assembly = detail["final_assemblies"][0] if detail["final_assemblies"] else None
    return {
        "project_id": project_id,
        "allow_publish": not missing and latest_assembly is not None and not latest_assembly.get("invalidated"),
        "selected_shot_count": len(selected),
        "missing_selected_takes": missing,
        "latest_assembly": latest_assembly,
        "next_step": "Rebuild final assembly and select missing takes before publishing." if missing else "Review final assembly, then configure the publish provider.",
    }


@router.get("/projects/{project_id}/status")
def v3_project_status(project_id: int):
    require_v3_enabled()
    try:
        return project_service.get_project_status(project_id).model_dump()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
