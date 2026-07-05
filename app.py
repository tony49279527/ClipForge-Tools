import json
import os
import shutil
import subprocess
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field, field_validator

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, Header
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import env_bootstrap  # noqa: F401

from clipforge_v3 import is_v3_enabled
from clipforge_v3.migrations import run_v3_migrations
from clipforge_v3.router import router as v3_router
from clipforge_v3.services.maintenance_service import is_maintenance_mode, maintenance_payload
from db import (
    create_clip_records,
    create_job,
    create_frame_image_version,
    create_storyboard_frames,
    create_template,
    delete_clips_for_job,
    delete_frame_image_versions,
    delete_storyboard_frames_for_job,
    delete_template,
    delete_usage_events,
    get_current_frame_image_version,
    get_frame_image_version,
    get_all_jobs,
    get_clip_rows_by_job_id,
    get_job_by_id,
    get_storyboard_frame,
    get_storyboard_frames,
    get_template,
    get_usage_totals_by_stage,
    list_frame_image_versions,
    list_templates,
    list_usage_events,
    set_current_frame_image_version,
    swap_storyboard_frame_positions,
    update_frame_image_version,
    update_job_fields,
    update_storyboard_frame,
)
from idea_core import generate_storyboard_prompts
from image_core import generate_storyboard_image
from usage_core import estimate_stage_cost, record_usage, refresh_job_usage_totals
from youtube_core import list_youtube_accounts
from video_core import OUTPUTS_DIR, ensure_runtime_dirs
from youtube_core import upload_youtube

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", str(BASE_DIR / "uploads"))).resolve()
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

ALLOWED_UPLOAD_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


@lru_cache(maxsize=1)
def get_app_version() -> str:
    env_version = (os.getenv("APP_VERSION") or "").strip()
    if env_version:
        return env_version

    cloud_run_revision = (os.getenv("K_REVISION") or "").strip()
    if cloud_run_revision:
        return cloud_run_revision

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(BASE_DIR),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "unknown"

@asynccontextmanager
async def lifespan(app: FastAPI):
    on_startup()
    yield


def on_startup() -> None:
    ensure_runtime_dirs()
    if is_v3_enabled():
        run_v3_migrations()


app = FastAPI(title="ClipForge Tools", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(v3_router)
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
WRITE_LIKE_GET_PREFIXES = ("/auth/", "/oauth")


@app.middleware("http")
async def maintenance_write_freeze(request: Request, call_next):
    path = request.url.path
    write_like_get = request.method.upper() == "GET" and path.startswith(WRITE_LIKE_GET_PREFIXES)
    if is_maintenance_mode() and (request.method.upper() in WRITE_METHODS or write_like_get):
        return JSONResponse(status_code=503, content=maintenance_payload())
    return await call_next(request)


def resolve_lang(request: Request) -> str:
    lang = request.query_params.get("lang", "zh").lower()
    return lang if lang in {"en", "zh"} else "zh"


def build_common_context(request: Request) -> dict:
    ensure_runtime_dirs()
    lang = resolve_lang(request)
    is_zh = lang == "zh"
    youtube_accounts = list_youtube_accounts()
    return {
        "request": request,
        "lang": lang,
        "is_zh": is_zh,
        "lang_switch_en": str(request.url.include_query_params(lang="en")),
        "lang_switch_zh": str(request.url.include_query_params(lang="zh")),
        "app_version": get_app_version(),
        "clipforge_v3_enabled": is_v3_enabled(),
        "youtube_accounts": youtube_accounts,
        "has_youtube_accounts": len(youtube_accounts) > 0,
        "status_label": lambda value: status_label(value, is_zh),
        "step_label": lambda value: current_step_label(value, is_zh),
        "image_status_label": lambda value: image_status_label(value, is_zh),
        "workflow_stage_label": lambda value: workflow_label(value, is_zh),
        "video_mode_label": lambda value: value_label(value, "video_mode", is_zh),
        "privacy_label": lambda value: value_label(value, "privacy", is_zh),
        "format_datetime": format_datetime_local,
    }


def format_datetime_local(value: Optional[str]) -> str:
    if not value:
        return "-"
    try:
        normalized = value.strip()
        if normalized.endswith("Z"):
            dt = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone(ZoneInfo("Asia/Shanghai"))
        return local_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return value


def workflow_label(stage: str, is_zh: bool) -> str:
    labels = {
        "idea_submitted": ("创意已提交", "Idea submitted"),
        "prompts_generating": ("正在生成分镜提示词", "Generating storyboard prompts"),
        "prompts_ready": ("分镜提示词已生成", "Storyboard prompts ready"),
        "images_generating": ("正在生成分镜图", "Generating storyboard images"),
        "images_ready": ("分镜图已生成", "Storyboard images ready"),
        "images_approved": ("分镜图已确认", "Storyboard images approved"),
        "videos_generating": ("正在生成视频", "Generating videos"),
        "videos_ready": ("视频已生成", "Videos ready"),
        "publishing": ("正在发布", "Publishing"),
        "succeeded": ("已完成", "Completed"),
        "failed": ("失败", "Failed"),
    }
    zh, en = labels.get(stage, (stage or "-", stage or "-"))
    return zh if is_zh else en


def status_label(status: str, is_zh: bool) -> str:
    labels = {
        "queued": ("排队中", "Queued"),
        "running": ("执行中", "Running"),
        "generating_clips": ("生成片段中", "Generating Clips"),
        "stitching": ("拼接中", "Stitching"),
        "uploading": ("上传中", "Uploading"),
        "succeeded": ("已完成", "Succeeded"),
        "failed": ("失败", "Failed"),
        "generating": ("生成中", "Generating"),
        "ready": ("已就绪", "Ready"),
        "approved": ("已确认", "Approved"),
    }
    zh, en = labels.get(status, (status or "-", status or "-"))
    return zh if is_zh else en


def image_status_label(status: str, is_zh: bool) -> str:
    labels = {
        "queued": ("待生成", "Queued"),
        "generating": ("生成中", "Generating"),
        "ready": ("已生成", "Ready"),
        "failed": ("失败", "Failed"),
    }
    zh, en = labels.get(status, (status or "-", status or "-"))
    return zh if is_zh else en


def current_step_label(step: str, is_zh: bool) -> str:
    step = step or "-"
    if step.startswith("Generating ") and " storyboard clips in parallel" in step:
        zh = step.replace("Generating ", "正在并行生成 ").replace(" storyboard clips in parallel", " 个分镜视频片段").replace(" workers", " 个并行线程")
        return zh if is_zh else step
    if step.startswith("Generating clip ") and " from storyboard" in step:
        zh = step.replace("Generating clip ", "正在生成分镜片段 ").replace(" from storyboard", "")
        return zh if is_zh else step
    if step.startswith("Generating ") and " clips in parallel" in step:
        zh = step.replace("Generating ", "正在并行生成 ").replace(" clips in parallel", " 个片段").replace(" workers", " 个并行线程")
        return zh if is_zh else step
    if step.startswith("Generating clip "):
        zh = step.replace("Generating clip ", "正在生成片段 ")
        en = step
        return zh if is_zh else en
    if step.startswith("Regenerating clip "):
        zh = step.replace("Regenerating clip ", "正在重生成片段 ")
        return zh if is_zh else step
    if step.startswith("Clip ") and step.endswith(" regenerated"):
        zh = step.replace("Clip ", "片段 ").replace(" regenerated", " 已重生成")
        return zh if is_zh else step
    labels = {
        "queued": ("排队中", "Queued"),
        "preparing prompts": ("正在准备提示词", "Preparing prompts"),
        "Generating bilingual storyboard prompts": ("正在生成双语分镜提示词", "Generating bilingual storyboard prompts"),
        "Storyboard prompts ready for review": ("分镜提示词已生成，等待审核", "Storyboard prompts ready for review"),
        "Prompt generation failed": ("提示词生成失败", "Prompt generation failed"),
        "Generating storyboard images": ("正在生成分镜图片", "Generating storyboard images"),
        "Storyboard images ready for review": ("分镜图片已生成，等待审核", "Storyboard images ready for review"),
        "Storyboard image generation failed": ("分镜图片生成失败", "Storyboard image generation failed"),
        "Uploading to YouTube": ("正在上传到 YouTube", "Uploading to YouTube"),
        "Published to YouTube": ("已发布到 YouTube", "Published to YouTube"),
        "Publishing failed": ("发布失败", "Publishing failed"),
        "Idea intake received": ("已接收创意输入", "Idea intake received"),
        "Regenerating storyboard prompts": ("正在重新生成分镜提示词", "Regenerating storyboard prompts"),
        "Storyboard prompts updated": ("分镜提示词已更新", "Storyboard prompts updated"),
        "Regenerating storyboard image": ("正在重新生成分镜图片", "Regenerating storyboard image"),
        "Storyboard review in progress": ("分镜审核进行中", "Storyboard review in progress"),
        "All storyboard frames approved": ("全部分镜图片已确认", "All storyboard frames approved"),
        "Queued for video generation": ("已加入视频生成队列", "Queued for video generation"),
        "Storyboard version changed": ("分镜版本已切换", "Storyboard version changed"),
        "Storyboard order updated": ("分镜顺序已更新", "Storyboard order updated"),
        "Prompt review updated": ("提示词审核状态已更新", "Prompt review updated"),
        "Video review updated": ("视频确认状态已更新", "Video review updated"),
        "Publish confirmation updated": ("发布确认状态已更新", "Publish confirmation updated"),
        "Concatenating clips": ("正在拼接视频片段", "Concatenating clips"),
        "Preparing approved storyboard frames": ("正在整理已确认的分镜图片", "Preparing approved storyboard frames"),
        "Video generation completed": ("视频生成已完成", "Video generation completed"),
        "Completed": ("已完成", "Completed"),
        "Failed": ("失败", "Failed"),
        "Rebuilding final video": ("正在重新生成最终视频", "Rebuilding final video"),
        "Local reference images were saved, but Seedance needs public image URLs. Configure object storage before uploaded files can be used as reference_image.": (
            "本地参考图已保存，但 Seedance 需要公网图片 URL。接入对象存储后，上传图片才能真正作为参考图使用。",
            "Local reference images were saved, but Seedance needs public image URLs. Configure object storage before uploaded files can be used as reference_image.",
        ),
    }
    zh, en = labels.get(step, (step or "-", step or "-"))
    return zh if is_zh else en


def value_label(value: str, kind: str, is_zh: bool) -> str:
    mapping = {
        "video_mode": {
            "shorts": ("短视频", "Shorts"),
            "long_video": ("长视频", "Long Video"),
        },
        "privacy": {
            "private": ("私密", "Private"),
            "unlisted": ("不公开列出", "Unlisted"),
            "public": ("公开", "Public"),
        },
    }
    labels = mapping.get(kind, {})
    zh, en = labels.get(value, (value or "-", value or "-"))
    return zh if is_zh else en


def _usage_tokens(usage: dict) -> tuple[int, int, int]:
    if not usage:
        return 0, 0, 0
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (input_tokens + output_tokens))
    return input_tokens, output_tokens, total_tokens


def sanitize_filename(filename: str) -> str:
    source = Path(filename).name
    stem = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in Path(source).stem)
    suffix = Path(source).suffix.lower()
    return f"{stem[:80] or 'upload'}{suffix}"


def save_uploaded_files(job_id: int, files: List[UploadFile]) -> List[str]:
    saved_paths: List[str] = []
    if not files:
        return saved_paths

    job_upload_dir = UPLOADS_DIR / str(job_id)
    job_upload_dir.mkdir(parents=True, exist_ok=True)

    for upload in files:
        if not upload.filename:
            continue
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"Unsupported file type: {upload.filename}")

        safe_name = sanitize_filename(upload.filename)
        final_name = f"{uuid.uuid4().hex}_{safe_name}"
        destination = job_upload_dir / final_name
        with destination.open("wb") as buffer:
            shutil.copyfileobj(upload.file, buffer)
        saved_paths.append(str(destination))

    return saved_paths


def create_storyboard_public_url(base_url: str, version_id: int) -> str:
    return f"{base_url.rstrip('/')}/v2/storyboards/versions/{version_id}/image"


def invalidate_v2_video_outputs(job_id: int) -> None:
    delete_clips_for_job(job_id)
    delete_usage_events(job_id, stages=["video_generation", "publishing"])
    update_job_fields(
        job_id,
        {
            "final_video_path": None,
            "youtube_url": None,
            "video_reviewed": 0,
            "publish_confirmed": 0,
        },
    )
    refresh_job_usage_totals(job_id)


def invalidate_v2_frame_assets(job_id: int, frame_id: int) -> None:
    delete_frame_image_versions(frame_id)
    delete_usage_events(job_id, stages=["image_generation"], entity_type="storyboard_frame", entity_id=frame_id)
    update_storyboard_frame(
        frame_id,
        {
            "image_status": "queued",
            "image_model": None,
            "image_remote_url": None,
            "image_local_path": None,
            "image_tokens": 0,
            "image_estimated_cost_cny": 0,
            "user_approved": 0,
            "error_message": None,
        },
    )
    refresh_job_usage_totals(job_id)


def generate_storyboard_prompts_job(job_id: int) -> None:
    job = get_job_by_id(job_id)
    if not job:
        return
    try:
        invalidate_v2_video_outputs(job_id)
        delete_usage_events(job_id, stages=["image_generation"])
        update_job_fields(
            job_id,
            {
                "status": "running",
                "workflow_stage": "prompts_generating",
                "current_step": "Generating bilingual storyboard prompts",
                "error_message": None,
            },
        )
        result = generate_storyboard_prompts(
            {
                "product_name": job["product_name"],
                "simple_idea": job["simple_idea"] or job["product_brief"],
                "target_audience": job["target_audience"],
                "clip_count": job["clip_count"],
                "video_mode": job["video_mode"],
                "ratio": job["ratio"],
                "style_preference": job["style_preference"],
            }
        )
        delete_storyboard_frames_for_job(job_id)
        create_storyboard_frames(job_id, result["frames"])
        usage = result.get("usage") or {}
        input_tokens, output_tokens, total_tokens = _usage_tokens(usage)
        if total_tokens or usage:
            record_usage(
                job_id=job_id,
                stage="prompt_generation",
                entity_type="job",
                entity_id=job_id,
                action="generate_prompts",
                model_name=os.getenv("OPENAI_TEXT_MODEL", "gpt-5-mini"),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                raw_usage=usage,
            )
        update_job_fields(
            job_id,
            {
                "status": "queued",
                "workflow_stage": "prompts_ready",
                "current_step": "Storyboard prompts ready for review",
                "prompt_reviewed": 0,
            },
        )
    except Exception as exc:
        update_job_fields(
            job_id,
            {
                "status": "failed",
                "workflow_stage": "failed",
                "current_step": "Prompt generation failed",
                "error_message": str(exc),
            },
        )


def generate_storyboard_images_job(job_id: int, base_url: str, frame_id: int = None) -> None:
    job = get_job_by_id(job_id)
    if not job:
        return
    frames = [get_storyboard_frame(frame_id)] if frame_id else get_storyboard_frames(job_id)
    frames = [frame for frame in frames if frame]
    if not frames:
        return
    try:
        update_job_fields(
            job_id,
            {
                "status": "running",
                "workflow_stage": "images_generating",
                "current_step": "Generating storyboard images",
                "error_message": None,
            },
        )
        for frame in frames:
            versions = list_frame_image_versions(frame["id"])
            next_version = (versions[0]["version_no"] + 1) if versions else 1
            update_storyboard_frame(frame["id"], {"image_status": "generating", "error_message": None})
            output_path = OUTPUTS_DIR / str(job_id) / "storyboards" / f"frame_{frame['clip_index']:02d}_v{next_version}.png"
            image_result = generate_storyboard_image(frame["prompt_en"] or frame["prompt_zh"], job["ratio"], output_path)
            tokens = int(image_result["total_tokens"] or 0)
            cost = estimate_stage_cost("image_generation", total_tokens=tokens, unit_count=1)
            version_id = create_frame_image_version(
                {
                    "frame_id": frame["id"],
                    "version_no": next_version,
                    "prompt_zh": frame["prompt_zh"],
                    "prompt_en": frame["prompt_en"],
                    "image_local_path": str(output_path),
                    "image_status": "ready",
                    "image_model": image_result["model"],
                    "tokens": tokens,
                    "estimated_cost_cny": cost,
                    "raw_usage_json": json.dumps(image_result.get("usage") or {}, ensure_ascii=False),
                    "is_current": 1,
                }
            )
            remote_url = create_storyboard_public_url(base_url, version_id)
            update_frame_image_version(version_id, {"image_remote_url": remote_url})
            update_storyboard_frame(
                frame["id"],
                {
                    "image_status": "ready",
                    "image_model": image_result["model"],
                    "image_remote_url": remote_url,
                    "image_local_path": str(output_path),
                    "image_tokens": tokens,
                    "image_estimated_cost_cny": cost,
                    "user_approved": 0,
                },
            )
            record_usage(
                job_id=job_id,
                stage="image_generation",
                entity_type="storyboard_frame",
                entity_id=frame["id"],
                action="generate_image" if next_version == 1 else "regenerate_image",
                model_name=image_result["model"],
                total_tokens=tokens,
                estimated_cost_cny=cost,
                raw_usage=image_result.get("usage") or {},
                unit_count=1,
            )

        all_frames = get_storyboard_frames(job_id)
        next_stage = "images_approved" if all(all_frames and int(frame["user_approved"] or 0) == 1 for frame in all_frames) else "images_ready"
        update_job_fields(
            job_id,
            {
                "status": "queued",
                "workflow_stage": next_stage,
                "current_step": "Storyboard images ready for review",
            },
        )
    except Exception as exc:
        if frame_id:
            update_storyboard_frame(frame_id, {"image_status": "failed", "error_message": str(exc)})
        update_job_fields(
            job_id,
            {
                "status": "failed",
                "workflow_stage": "failed",
                "current_step": "Storyboard image generation failed",
                "error_message": str(exc),
            },
        )


def publish_v2_job(job_id: int) -> None:
    job = get_job_by_id(job_id)
    if not job:
        return
    try:
        frames = get_storyboard_frames(job_id)
        if int(job["prompt_reviewed"] or 0) != 1 or any(int(frame["user_approved"] or 0) != 1 for frame in frames):
            raise RuntimeError("Prompt and storyboard approvals are no longer valid for publishing.")
        if int(job["upload_to_youtube"] or 0) != 1:
            raise RuntimeError("This job is not configured for YouTube publishing.")
        final_video_path = Path(job["final_video_path"]) if job["final_video_path"] else None
        if not final_video_path or not final_video_path.exists():
            raise RuntimeError("Final video is not ready for publishing.")
        if int(job["video_reviewed"] or 0) != 1:
            raise RuntimeError("Video has not been approved for publishing yet.")
        if int(job["publish_confirmed"] or 0) != 1:
            raise RuntimeError("Publish confirmation is required before YouTube upload.")
        update_job_fields(job_id, {"status": "uploading", "workflow_stage": "publishing", "current_step": "Uploading to YouTube"})
        youtube_url = upload_youtube(
            video_path=final_video_path,
            title=job["youtube_title"],
            description=job["youtube_description"],
            tags=[job["product_name"], job["project_name"], "tools", "amazon"],
            privacy=job["privacy"],
            account_id=job["youtube_account_id"] or None,
        )
        record_usage(
            job_id=job_id,
            stage="publishing",
            entity_type="job",
            entity_id=job_id,
            action="upload_youtube",
            model_name="youtube.videos.insert",
            total_tokens=0,
            estimated_cost_cny=0,
        )
        update_job_fields(
            job_id,
            {
                "status": "succeeded",
                "workflow_stage": "succeeded",
                "current_step": "Published to YouTube",
                "youtube_url": youtube_url,
            },
        )
    except Exception as exc:
        update_job_fields(job_id, {"status": "failed", "workflow_stage": "failed", "current_step": "Publishing failed", "error_message": str(exc)})


@app.get("/")
def index(request: Request):
    sample_job = {
        "project_name": "Cordless Drill Demo Workflow",
        "product_name": "20V Cordless Power Drill Driver Kit",
        "amazon_url": "https://www.amazon.com/dp/B0DRILL2024",
        "product_brief": (
            "20V cordless drill driver kit with 2 batteries, variable speed trigger, LED work light, "
            "3/8-inch keyless chuck, and compact ergonomic grip. Show a clean garage workflow drilling "
            "pilot holes in a pine board, driving wood screws smoothly, and highlighting control, torque, "
            "and portability for DIY homeowners and Amazon power tool shoppers."
        ),
        "video_mode": "long_video",
        "ratio": "9:16",
        "clip_duration": 10,
        "clip_count": 4,
        "resolution": "720p",
        "privacy": "unlisted",
        "youtube_title": "Cordless Drill Demo: Drill, Drive, and Finish in 4 Steps",
        "youtube_description": (
            "Watch a 4-step cordless drill demo covering the hook, product closeup, bit change, drilling, "
            "and screw driving result. Built for DIY homeowners, garage workshops, and Amazon power tool buyers."
        ),
        "reference_image_urls": "\n".join(
            [
                "https://source.unsplash.com/1600x900/?cordless-drill",
                "https://source.unsplash.com/1600x900/?power-drill,workbench",
                "https://source.unsplash.com/1600x900/?drill-driver,woodworking",
                "https://source.unsplash.com/1600x900/?garage,drill,tool",
            ]
        ),
        "upload_to_youtube": True,
        "stitch_final_video": True,
    }
    context = build_common_context(request)
    context.update(
        {
            "defaults": {
                "video_mode": "long_video",
                "ratio": "9:16",
                "clip_duration": 10,
                "clip_count": 4,
                "resolution": "720p",
                "privacy": "unlisted",
            },
            "sample_job": sample_job,
            "youtube_notice": (
                "受 YouTube Data API 限制影响，2020 年 7 月 28 日后创建且未经审核的 API 项目，即使选择“不公开列出”或“公开”，最终也可能被强制设为“私密”。"
                if context["is_zh"]
                else "Due to YouTube Data API restrictions, videos uploaded via videos.insert from projects created after July 28, 2020 and not yet audited may be forced to Private even if you select unlisted or public."
            ),
        }
    )
    return templates.TemplateResponse(
        request,
        "index.html",
        context,
    )


@app.post("/jobs")
async def create_job_view(
    request: Request,
    project_name: str = Form(...),
    product_name: str = Form(...),
    amazon_url: str = Form(""),
    product_brief: str = Form(...),
    video_mode: str = Form(...),
    ratio: str = Form(...),
    clip_duration: int = Form(...),
    clip_count: int = Form(...),
    resolution: str = Form(...),
    youtube_title: str = Form(...),
    youtube_description: str = Form(""),
    youtube_account_id: str = Form(""),
    privacy: str = Form(...),
    reference_image_urls: str = Form(""),
    upload_to_youtube: bool = Form(False),
    stitch_final_video: bool = Form(True),
    reference_images: List[UploadFile] = File(default=[]),
):
    if upload_to_youtube and not youtube_account_id.strip():
        # Fallback: if upload is enabled but no account selected, try to pick the first one automatically
        from youtube_core import list_youtube_accounts
        accounts = list_youtube_accounts()
        if accounts:
            youtube_account_id = accounts[0]["account_id"]
        else:
            raise HTTPException(status_code=400, detail="YouTube account selection is required when upload is enabled, but no accounts are configured.")
    raw_urls = [line.strip() for line in reference_image_urls.splitlines() if line.strip()]
    job_payload = {
        "project_name": project_name.strip(),
        "product_name": product_name.strip(),
        "amazon_url": amazon_url.strip(),
        "product_brief": product_brief.strip(),
        "video_mode": video_mode,
        "ratio": ratio,
        "clip_duration": clip_duration,
        "clip_count": clip_count,
        "resolution": resolution,
        "youtube_title": youtube_title.strip(),
        "youtube_account_id": youtube_account_id.strip(),
        "youtube_description": youtube_description.strip(),
        "privacy": privacy,
        "upload_to_youtube": 1 if upload_to_youtube else 0,
        "stitch_final_video": 1 if stitch_final_video else 0,
        "reference_image_urls_json": json.dumps(raw_urls, ensure_ascii=False),
        "status": "queued",
        "current_step": "queued",
        "uploaded_images_note": "",
    }
    job_id = create_job(job_payload)
    create_clip_records(job_id=job_id, clip_count=clip_count)

    saved_uploads = save_uploaded_files(job_id, reference_images)
    if saved_uploads:
        note = (
            "Local reference images were saved, but Seedance needs public image URLs. "
            "Configure object storage before uploaded files can be used as reference_image."
        )
        update_job_fields(job_id, {"uploaded_images_note": note})

    # Enqueue via RQ task queue for persistent, concurrent execution.
    from task_queue import enqueue_video_job
    enqueue_video_job(job_id)
    lang = resolve_lang(request)
    return RedirectResponse(url=f"/jobs/{job_id}?lang={lang}", status_code=303)


@app.get("/jobs")
def jobs(request: Request):
    rows = get_all_jobs()
    context = build_common_context(request)
    context.update({"jobs": rows})
    return templates.TemplateResponse(request, "jobs.html", context)


@app.get("/jobs/{job_id}")
def job_detail(request: Request, job_id: int):
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    clips = get_clip_rows_by_job_id(job_id)
    context = build_common_context(request)
    context.update({"job": job, "clips": clips})
    return templates.TemplateResponse(request, "job.html", context)


@app.get("/jobs/{job_id}/status")
def job_status(request: Request, job_id: int):
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    is_zh = resolve_lang(request) == "zh"
    clips = [dict(row) for row in get_clip_rows_by_job_id(job_id)]
    for clip in clips:
        clip["status_label"] = status_label(clip.get("status"), is_zh)
    queue = None
    try:
        from task_queue import get_job_status as get_rq_job_status

        queue = get_rq_job_status(job_id)
    except Exception as exc:
        queue = {"status": "unavailable", "error": str(exc)}
    payload = dict(job)
    payload["status_label"] = status_label(payload.get("status"), is_zh)
    payload["step_label"] = current_step_label(payload.get("current_step"), is_zh)
    payload["uploaded_images_note_label"] = current_step_label(payload.get("uploaded_images_note"), is_zh)
    return {"job": payload, "clips": clips, "queue": queue}


@app.get("/jobs/{job_id}/video")
def job_video(job_id: int):
    """Serve the final video file for in-browser preview."""
    job = get_job_by_id(job_id)
    if not job or not job["final_video_path"]:
        raise HTTPException(status_code=404, detail="No video available")
    video_path = Path(job["final_video_path"])
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found")
    return FileResponse(video_path, media_type="video/mp4")


@app.get("/health")
@app.get("/healthz")
def healthz():
    return {"ok": True}


# ── Product Scraper API ──
@app.get("/api/scrape")
async def scrape_product_api(request: Request, url: str = ""):
    """Scrape product info from a URL. Returns structured data for auto-filling job forms."""
    if not url:
        raise HTTPException(status_code=400, detail="url parameter is required")

    from product_scraper import scrape_product
    from prompt_builder import generate_prompts_from_url, product_audience_guess

    product = await scrape_product(url)
    if product.error and not product.title:
        raise HTTPException(status_code=422, detail=f"Failed to scrape: {product.error}")

    prompt_set = generate_prompts_from_url(product)

    context = build_common_context(request)
    return {
        "url": product.url,
        "platform": product.platform,
        "title": product.title,
        "description": product.description[:500] if product.description else "",
        "price": product.price,
        "features": product.features[:10],
        "image_urls": product.image_urls[:6],
        "category_hints": product.category_hints,
        "target_audience": prompt_set["target_audience"],
        "suggested_prompt": prompt_set["youtube_prompt"][:2000],
        "error": product.error,
    }


@app.get("/v2")
def v2_index(request: Request):
    context = build_common_context(request)
    context.update(
        {
            "sample_idea": {
                "idea_title": "Cordless Drill Storyboard Flow",
                "project_name": "ClipForge 2.0 Drill Sample",
                "product_name": "20V Cordless Power Drill Driver Kit",
                "simple_idea": "做一个更像教程型广告的 Shorts，先展示手拧螺丝费力和木板打孔慢，再展示电钻装上批头、快速钻孔、顺滑拧入木螺丝，最后给出干净利落的成品特写。",
                "target_audience": "美国 DIY 家装用户、车库木工爱好者、Amazon 电动工具买家",
                "video_mode": "long_video",
                "ratio": "9:16",
                "clip_count": 4,
                "clip_duration": 10,
                "resolution": "720p",
                "style_preference": "真实美国车库、木工工作台、手部动作明确、钻孔木屑细节清晰、结果对比强",
                "youtube_title": "4-Step Cordless Drill Workflow",
                "youtube_description": "Storyboard-first workflow demo for a cordless drill driver product video.",
                "privacy": "unlisted",
            }
        }
    )
    return templates.TemplateResponse(request, "v2_index.html", context)


@app.post("/v2/jobs")
async def create_v2_job(
    request: Request,
    idea_title: str = Form(...),
    project_name: str = Form(...),
    product_name: str = Form(...),
    simple_idea: str = Form(...),
    target_audience: str = Form(""),
    video_mode: str = Form(...),
    ratio: str = Form(...),
    clip_duration: int = Form(...),
    clip_count: int = Form(...),
    resolution: str = Form(...),
    style_preference: str = Form(""),
    youtube_title: str = Form(...),
    youtube_description: str = Form(""),
    youtube_account_id: str = Form(""),
    privacy: str = Form(...),
    upload_to_youtube: bool = Form(False),
):
    if upload_to_youtube and not youtube_account_id.strip():
        accounts = list_youtube_accounts()
        if accounts:
            youtube_account_id = accounts[0]["account_id"]
    job_id = create_job(
        {
            "project_name": project_name.strip(),
            "product_name": product_name.strip(),
            "amazon_url": "",
            "product_brief": simple_idea.strip(),
            "video_mode": video_mode,
            "ratio": ratio,
            "clip_duration": clip_duration,
            "clip_count": clip_count,
            "resolution": resolution,
            "youtube_title": youtube_title.strip(),
            "youtube_account_id": youtube_account_id.strip(),
            "youtube_description": youtube_description.strip(),
            "privacy": privacy,
            "upload_to_youtube": 1 if upload_to_youtube else 0,
            "stitch_final_video": 1,
            "reference_image_urls_json": "[]",
            "status": "queued",
            "current_step": "Idea intake received",
            "workflow_version": "2.0",
            "workflow_stage": "idea_submitted",
            "idea_title": idea_title.strip(),
            "simple_idea": simple_idea.strip(),
            "target_audience": target_audience.strip(),
            "language": resolve_lang(request),
            "style_preference": style_preference.strip(),
        }
    )
    from task_queue import enqueue_storyboard_prompts_job
    enqueue_storyboard_prompts_job(job_id)
    return RedirectResponse(url=f"/v2/jobs/{job_id}?lang={resolve_lang(request)}", status_code=303)


@app.get("/v2/jobs")
def v2_jobs(request: Request):
    rows = [row for row in get_all_jobs() if (row["workflow_version"] or "1.0") == "2.0"]
    context = build_common_context(request)
    context.update({"jobs": rows})
    return templates.TemplateResponse(request, "v2_jobs.html", context)


@app.get("/v2/jobs/{job_id}")
def v2_job_detail(request: Request, job_id: int):
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    frames = get_storyboard_frames(job_id)
    for index, frame in enumerate(frames):
        frame_versions = list_frame_image_versions(frame["id"])
        frames[index] = dict(frame)
        frames[index]["versions"] = [dict(version) for version in frame_versions]
    usage_events = [dict(row) for row in list_usage_events(job_id)]
    context = build_common_context(request)
    context.update(
        {
            "job": job,
            "frames": frames,
            "clips": get_clip_rows_by_job_id(job_id),
            "usage_events": usage_events,
            "stage_label": workflow_label(job["workflow_stage"], context["is_zh"]),
            "usage_totals": get_usage_totals_by_stage(job_id),
        }
    )
    return templates.TemplateResponse(request, "v2_job.html", context)


@app.get("/v2/jobs/{job_id}/status")
def v2_job_status(request: Request, job_id: int):
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    lang_is_zh = resolve_lang(request) == "zh"
    frames = [dict(frame) for frame in get_storyboard_frames(job_id)]
    for frame in frames:
        frame["image_status_label"] = image_status_label(frame.get("image_status"), lang_is_zh)
    clips = [dict(row) for row in get_clip_rows_by_job_id(job_id)]
    for clip in clips:
        clip["status_label"] = status_label(clip.get("status"), lang_is_zh)
    usage_events = [dict(row) for row in list_usage_events(job_id)]
    queue = None
    try:
        from task_queue import get_job_status as get_rq_job_status

        queue = get_rq_job_status(job_id)
    except Exception as exc:
        queue = {"status": "unavailable", "error": str(exc)}
    payload = dict(job)
    payload["status_label"] = status_label(payload.get("status"), lang_is_zh)
    payload["step_label"] = current_step_label(payload.get("current_step"), lang_is_zh)
    return {
        "job": payload,
        "stage_label": workflow_label(job["workflow_stage"], lang_is_zh),
        "frames": frames,
        "clips": clips,
        "usage_events": usage_events,
        "usage_totals": get_usage_totals_by_stage(job_id),
        "queue": queue,
    }


@app.post("/v2/jobs/{job_id}/regenerate-prompts")
def regenerate_v2_prompts(request: Request, job_id: int):
    invalidate_v2_video_outputs(job_id)
    delete_usage_events(job_id, stages=["image_generation"])
    refresh_job_usage_totals(job_id)
    update_job_fields(
        job_id,
        {
            "workflow_stage": "prompts_generating",
            "current_step": "Regenerating storyboard prompts",
            "prompt_reviewed": 0,
            "video_reviewed": 0,
            "publish_confirmed": 0,
        },
    )
    from task_queue import enqueue_storyboard_prompts_job
    enqueue_storyboard_prompts_job(job_id)
    return RedirectResponse(url=f"/v2/jobs/{job_id}?lang={resolve_lang(request)}", status_code=303)


@app.post("/v2/frames/{frame_id}/update-prompts")
async def update_v2_frame_prompts(
    request: Request,
    frame_id: int,
    prompt_zh: str = Form(...),
    prompt_en: str = Form(...),
):
    frame = get_storyboard_frame(frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="Storyboard frame not found")
    update_storyboard_frame(
        frame_id,
        {
            "prompt_zh": prompt_zh.strip(),
            "prompt_en": prompt_en.strip(),
            "prompt_version": int(frame["prompt_version"] or 1) + 1,
            "user_approved": 0,
        },
    )
    invalidate_v2_frame_assets(frame["job_id"], frame_id)
    invalidate_v2_video_outputs(frame["job_id"])
    update_job_fields(
        frame["job_id"],
        {
            "workflow_stage": "prompts_ready",
            "current_step": "Storyboard prompts updated",
            "prompt_reviewed": 0,
            "video_reviewed": 0,
            "publish_confirmed": 0,
        },
    )
    return RedirectResponse(url=f"/v2/jobs/{frame['job_id']}?lang={resolve_lang(request)}", status_code=303)


@app.post("/v2/jobs/{job_id}/generate-images")
def generate_v2_images(request: Request, job_id: int):
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if int(job["prompt_reviewed"] or 0) != 1:
        raise HTTPException(status_code=400, detail="Approve storyboard prompts before generating images.")
    invalidate_v2_video_outputs(job_id)
    base_url = str(request.base_url).rstrip("/")
    update_job_fields(
        job_id,
        {
            "workflow_stage": "images_generating",
            "current_step": "Generating storyboard images",
            "prompt_reviewed": 1,
            "video_reviewed": 0,
            "publish_confirmed": 0,
        },
    )
    from task_queue import enqueue_storyboard_images_job
    enqueue_storyboard_images_job(job_id, base_url)
    return RedirectResponse(url=f"/v2/jobs/{job_id}?lang={resolve_lang(request)}", status_code=303)


@app.post("/v2/frames/{frame_id}/regenerate-image")
def regenerate_v2_image(request: Request, frame_id: int):
    frame = get_storyboard_frame(frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="Storyboard frame not found")
    invalidate_v2_video_outputs(frame["job_id"])
    base_url = str(request.base_url).rstrip("/")
    update_job_fields(
        frame["job_id"],
        {
            "current_step": "Regenerating storyboard image",
            "prompt_reviewed": 1,
            "video_reviewed": 0,
            "publish_confirmed": 0,
        },
    )
    from task_queue import enqueue_storyboard_images_job
    enqueue_storyboard_images_job(frame["job_id"], base_url, frame_id)
    return RedirectResponse(url=f"/v2/jobs/{frame['job_id']}?lang={resolve_lang(request)}", status_code=303)


@app.post("/v2/frames/{frame_id}/approve")
def approve_v2_frame(request: Request, frame_id: int):
    frame = get_storyboard_frame(frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="Storyboard frame not found")
    approved = 0 if int(frame["user_approved"] or 0) == 1 else 1
    invalidate_v2_video_outputs(frame["job_id"])
    update_storyboard_frame(frame_id, {"user_approved": approved})
    frames = get_storyboard_frames(frame["job_id"])
    next_stage = "images_approved" if frames and all(int(item["user_approved"] or 0) == 1 for item in frames) else "images_ready"
    update_job_fields(
        frame["job_id"],
        {
            "workflow_stage": next_stage,
            "current_step": "Storyboard review in progress",
            "prompt_reviewed": 1,
            "video_reviewed": 0,
            "publish_confirmed": 0,
        },
    )
    return RedirectResponse(url=f"/v2/jobs/{frame['job_id']}?lang={resolve_lang(request)}", status_code=303)


@app.post("/v2/jobs/{job_id}/approve-all")
def approve_all_v2(request: Request, job_id: int):
    frames = get_storyboard_frames(job_id)
    if not frames:
        raise HTTPException(status_code=404, detail="No storyboard frames found")
    invalidate_v2_video_outputs(job_id)
    for frame in frames:
        update_storyboard_frame(frame["id"], {"user_approved": 1})
    update_job_fields(
        job_id,
        {
            "workflow_stage": "images_approved",
            "current_step": "All storyboard frames approved",
            "prompt_reviewed": 1,
            "video_reviewed": 0,
            "publish_confirmed": 0,
        },
    )
    return RedirectResponse(url=f"/v2/jobs/{job_id}?lang={resolve_lang(request)}", status_code=303)


@app.post("/v2/jobs/{job_id}/launch-video")
def launch_v2_video(request: Request, job_id: int):
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if int(job["prompt_reviewed"] or 0) != 1:
        raise HTTPException(status_code=400, detail="Approve storyboard prompts before generating video.")
    frames = get_storyboard_frames(job_id)
    if not frames or any(int(frame["user_approved"] or 0) != 1 for frame in frames):
        raise HTTPException(status_code=400, detail="All storyboard images must be approved before video generation.")
    update_job_fields(
        job_id,
        {
            "prompt_reviewed": 1,
            "video_reviewed": 0,
            "publish_confirmed": 0,
            "current_step": "Queued for video generation",
        },
    )
    from task_queue import enqueue_storyboard_video_job
    enqueue_storyboard_video_job(job_id)
    return RedirectResponse(url=f"/v2/jobs/{job_id}?lang={resolve_lang(request)}", status_code=303)


@app.post("/v2/jobs/{job_id}/frames/{frame_id}/set-version/{version_id}")
def set_v2_frame_version(request: Request, job_id: int, frame_id: int, version_id: int):
    version = get_frame_image_version(version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Image version not found")
    try:
        set_current_frame_image_version(frame_id, version_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    invalidate_v2_video_outputs(job_id)
    update_storyboard_frame(frame_id, {"user_approved": 0})
    update_job_fields(
        job_id,
        {
            "workflow_stage": "images_ready",
            "current_step": "Storyboard version changed",
            "prompt_reviewed": 1,
            "video_reviewed": 0,
            "publish_confirmed": 0,
        },
    )
    return RedirectResponse(url=f"/v2/jobs/{job_id}?lang={resolve_lang(request)}", status_code=303)


@app.post("/v2/jobs/{job_id}/frames/{frame_id}/move-{direction}")
def move_v2_frame(request: Request, job_id: int, frame_id: int, direction: str):
    if direction not in ("up", "down"):
        raise HTTPException(status_code=400, detail="Direction must be up or down")
    frame = get_storyboard_frame(frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="Storyboard frame not found")
    frames = get_storyboard_frames(job_id)
    sorted_frames = sorted(frames, key=lambda f: f["clip_index"])
    current_idx = next((i for i, f in enumerate(sorted_frames) if f["id"] == frame_id), -1)
    if current_idx == -1:
        raise HTTPException(status_code=404, detail="Frame not found in job")
    if direction == "up" and current_idx == 0:
        raise HTTPException(status_code=400, detail="Already the first frame")
    if direction == "down" and current_idx == len(sorted_frames) - 1:
        raise HTTPException(status_code=400, detail="Already the last frame")
    swap_with = sorted_frames[current_idx - 1] if direction == "up" else sorted_frames[current_idx + 1]
    swap_storyboard_frame_positions(frame_id, swap_with["id"])
    invalidate_v2_video_outputs(job_id)
    update_job_fields(
        job_id,
        {
            "current_step": "Storyboard order updated",
            "prompt_reviewed": 1,
            "video_reviewed": 0,
            "publish_confirmed": 0,
        },
    )
    return RedirectResponse(url=f"/v2/jobs/{job_id}?lang={resolve_lang(request)}", status_code=303)


@app.post("/v2/jobs/{job_id}/publish")
def publish_v2(request: Request, job_id: int):
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    frames = get_storyboard_frames(job_id)
    if int(job["prompt_reviewed"] or 0) != 1 or any(int(frame["user_approved"] or 0) != 1 for frame in frames):
        raise HTTPException(status_code=400, detail="Prompt and storyboard approvals must still be valid before publishing.")
    if int(job["upload_to_youtube"] or 0) != 1:
        raise HTTPException(status_code=400, detail="YouTube publishing is disabled for this job.")
    if int(job["video_reviewed"] or 0) != 1 or int(job["publish_confirmed"] or 0) != 1:
        raise HTTPException(status_code=400, detail="Video review and publish confirmation are required before publishing.")
    from task_queue import enqueue_publish_job
    enqueue_publish_job(job_id)
    return RedirectResponse(url=f"/v2/jobs/{job_id}?lang={resolve_lang(request)}", status_code=303)


@app.post("/v2/jobs/{job_id}/confirm-prompts")
def confirm_v2_prompts(request: Request, job_id: int):
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    frames = get_storyboard_frames(job_id)
    if not frames:
        raise HTTPException(status_code=400, detail="No storyboard prompts are ready yet.")
    if any(not (frame["prompt_zh"] or "").strip() or not (frame["prompt_en"] or "").strip() for frame in frames):
        raise HTTPException(status_code=400, detail="All storyboard prompt cards must have both Chinese and English prompts.")
    reviewed = 0 if int(job["prompt_reviewed"] or 0) == 1 else 1
    next_stage = "images_ready" if reviewed == 1 else "prompts_ready"
    update_job_fields(
        job_id,
        {
            "prompt_reviewed": reviewed,
            "workflow_stage": next_stage,
            "current_step": "Prompt review updated",
            "video_reviewed": 0,
            "publish_confirmed": 0,
        },
    )
    return RedirectResponse(url=f"/v2/jobs/{job_id}?lang={resolve_lang(request)}", status_code=303)


@app.post("/v2/jobs/{job_id}/clips/{clip_index}/regenerate")
def regenerate_v2_clip(request: Request, job_id: int, clip_index: int):
    from task_queue import enqueue_storyboard_single_clip_job

    enqueue_storyboard_single_clip_job(job_id, clip_index)
    return RedirectResponse(url=f"/v2/jobs/{job_id}?lang={resolve_lang(request)}", status_code=303)


@app.post("/v2/jobs/{job_id}/confirm-video")
def confirm_v2_video(request: Request, job_id: int):
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    frames = get_storyboard_frames(job_id)
    if int(job["prompt_reviewed"] or 0) != 1:
        raise HTTPException(status_code=400, detail="Approve storyboard prompts before approving the video.")
    if not frames or any(int(frame["user_approved"] or 0) != 1 for frame in frames):
        raise HTTPException(status_code=400, detail="All storyboard images must remain approved before approving the video.")
    if not job["final_video_path"]:
        raise HTTPException(status_code=400, detail="Final video is not ready yet.")
    reviewed = 0 if int(job["video_reviewed"] or 0) == 1 else 1
    update_job_fields(
        job_id,
        {
            "video_reviewed": reviewed,
            "publish_confirmed": 0 if reviewed == 0 else int(job["publish_confirmed"] or 0),
            "current_step": "Video review updated",
        },
    )
    return RedirectResponse(url=f"/v2/jobs/{job_id}?lang={resolve_lang(request)}", status_code=303)


@app.post("/v2/jobs/{job_id}/confirm-publish")
def confirm_v2_publish(request: Request, job_id: int):
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    frames = get_storyboard_frames(job_id)
    if int(job["prompt_reviewed"] or 0) != 1:
        raise HTTPException(status_code=400, detail="Approve storyboard prompts before confirming publish.")
    if not frames or any(int(frame["user_approved"] or 0) != 1 for frame in frames):
        raise HTTPException(status_code=400, detail="All storyboard images must remain approved before confirming publish.")
    if not job["final_video_path"]:
        raise HTTPException(status_code=400, detail="Final video is not ready yet.")
    if int(job["video_reviewed"] or 0) != 1:
        raise HTTPException(status_code=400, detail="Approve the video before confirming publish.")
    confirmed = 0 if int(job["publish_confirmed"] or 0) == 1 else 1
    update_job_fields(job_id, {"publish_confirmed": confirmed, "current_step": "Publish confirmation updated"})
    return RedirectResponse(url=f"/v2/jobs/{job_id}?lang={resolve_lang(request)}", status_code=303)


@app.get("/v2/jobs/{job_id}/video")
def v2_job_video(job_id: int):
    """Serve the final video file for v2 job in-browser preview."""
    job = get_job_by_id(job_id)
    if not job or not job["final_video_path"]:
        raise HTTPException(status_code=404, detail="No video available")
    video_path = Path(job["final_video_path"])
    if not video_path.exists():
        raise HTTPException(status_code=404, detail="Video file not found")
    return FileResponse(video_path, media_type="video/mp4")


@app.get("/v2/jobs/{job_id}/clips/{clip_index}/video")
def v2_clip_video(job_id: int, clip_index: int):
    clip_rows = get_clip_rows_by_job_id(job_id)
    clip = next((row for row in clip_rows if int(row["clip_index"]) == int(clip_index)), None)
    if not clip or not clip["local_path"]:
        raise HTTPException(status_code=404, detail="Clip video not found")
    clip_path = Path(clip["local_path"])
    if not clip_path.exists():
        raise HTTPException(status_code=404, detail="Clip video file missing")
    return FileResponse(clip_path, media_type="video/mp4")


@app.get("/v2/storyboards/versions/{version_id}/image")
def get_storyboard_image(version_id: int):
    version = get_frame_image_version(version_id)
    if not version or not version["image_local_path"]:
        raise HTTPException(status_code=404, detail="Storyboard image not found")
    image_path = Path(version["image_local_path"])
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Storyboard image file missing")
    return FileResponse(image_path)


# ── Template Presets API ──────────────────────────────────────────

@app.get("/api/templates")
def api_list_templates():
    """Return all saved templates as JSON."""
    rows = list_templates()
    return [dict(row) for row in rows]


@app.get("/api/templates/{template_id}")
def api_get_template(template_id: int):
    row = get_template(template_id)
    if not row:
        raise HTTPException(status_code=404, detail="Template not found")
    return dict(row)


@app.post("/api/templates")
async def api_create_template(request: Request):
    """Save current form values as a template."""
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Template name is required")
    template_id = create_template(
        {
            "name": name,
            "project_name": body.get("project_name", ""),
            "product_name": body.get("product_name", ""),
            "simple_idea": body.get("simple_idea", ""),
            "target_audience": body.get("target_audience", ""),
            "video_mode": body.get("video_mode", ""),
            "ratio": body.get("ratio", ""),
            "clip_count": int(body.get("clip_count", 4)),
            "clip_duration": int(body.get("clip_duration", 10)),
            "resolution": body.get("resolution", ""),
            "style_preference": body.get("style_preference", ""),
            "youtube_title": body.get("youtube_title", ""),
            "youtube_description": body.get("youtube_description", ""),
            "privacy": body.get("privacy", ""),
        }
    )
    return {"id": template_id, "name": name}


@app.delete("/api/templates/{template_id}")
def api_delete_template(template_id: int):
    delete_template(template_id)
    return {"ok": True}


class ExternalJobPayload(BaseModel):
    project_name: str = Field(..., min_length=1, max_length=200)
    product_name: str = Field(..., min_length=1, max_length=200)
    product_brief: str = Field(..., min_length=1, max_length=4000)
    amazon_url: Optional[str] = Field("", max_length=2000)
    video_mode: Optional[str] = "shorts"
    ratio: Optional[str] = "9:16"
    clip_duration: Optional[int] = 5
    clip_count: Optional[int] = 2
    resolution: Optional[str] = "720p"
    youtube_title: Optional[str] = Field(None, max_length=100)
    youtube_description: Optional[str] = Field("", max_length=5000)
    youtube_account_id: Optional[str] = Field("", max_length=200)
    privacy: Optional[str] = "private"
    upload_to_youtube: Optional[bool] = False
    stitch_final_video: Optional[bool] = True
    reference_image_urls: Optional[List[str]] = Field(default_factory=list)

    @field_validator("video_mode")
    @classmethod
    def validate_video_mode(cls, value: Optional[str]) -> str:
        allowed = {"shorts", "long_video"}
        if value not in allowed:
            raise ValueError(f"video_mode must be one of {sorted(allowed)}")
        return value

    @field_validator("ratio")
    @classmethod
    def validate_ratio(cls, value: Optional[str]) -> str:
        allowed = {"9:16", "16:9"}
        if value not in allowed:
            raise ValueError(f"ratio must be one of {sorted(allowed)}")
        return value

    @field_validator("clip_duration")
    @classmethod
    def validate_clip_duration(cls, value: Optional[int]) -> int:
        allowed = {5, 10, 15}
        if value not in allowed:
            raise ValueError(f"clip_duration must be one of {sorted(allowed)}")
        return int(value)

    @field_validator("clip_count")
    @classmethod
    def validate_clip_count(cls, value: Optional[int]) -> int:
        allowed = {1, 2, 4, 20}
        if value not in allowed:
            raise ValueError(f"clip_count must be one of {sorted(allowed)}")
        return int(value)

    @field_validator("resolution")
    @classmethod
    def validate_resolution(cls, value: Optional[str]) -> str:
        allowed = {"480p", "720p"}
        if value not in allowed:
            raise ValueError(f"resolution must be one of {sorted(allowed)}")
        return value

    @field_validator("privacy")
    @classmethod
    def validate_privacy(cls, value: Optional[str]) -> str:
        allowed = {"private", "unlisted", "public"}
        if value not in allowed:
            raise ValueError(f"privacy must be one of {sorted(allowed)}")
        return value

    @field_validator("reference_image_urls")
    @classmethod
    def validate_reference_image_urls(cls, value: Optional[List[str]]) -> List[str]:
        urls = value or []
        if len(urls) > 20:
            raise ValueError("reference_image_urls cannot contain more than 20 items")
        normalized: List[str] = []
        for url in urls:
            trimmed = (url or "").strip()
            if not trimmed:
                continue
            if len(trimmed) > 2000:
                raise ValueError("reference_image_url is too long")
            normalized.append(trimmed)
        return normalized


@app.post("/api/external/jobs")
async def api_create_external_job(
    payload: ExternalJobPayload,
    x_api_key: Optional[str] = Header(None, alias="X-API-Key")
):
    """Secure JSON endpoint for external systems (e.g. Lobster, Hermes, OpenClaw) to enqueue jobs."""
    # API key check
    configured_api_key = (os.getenv("CLIPFORGE_API_KEY") or "").strip()
    if not configured_api_key:
        raise HTTPException(status_code=503, detail="External API is disabled because CLIPFORGE_API_KEY is not configured.")
    if x_api_key != configured_api_key:
        raise HTTPException(status_code=401, detail="Unauthorized client: Invalid API Key")

    youtube_account_id = (payload.youtube_account_id or "").strip()
    accounts = list_youtube_accounts()
    
    if payload.upload_to_youtube:
        if not accounts:
            raise HTTPException(
                status_code=400,
                detail="YouTube upload is enabled, but no YouTube accounts are configured on this server."
            )
        
        valid_ids = {a["account_id"] for a in accounts}
        if not youtube_account_id:
            raise HTTPException(status_code=400, detail="youtube_account_id is required when upload_to_youtube is true.")
        if youtube_account_id not in valid_ids:
            raise HTTPException(status_code=400, detail="youtube_account_id is not a configured account on this server.")

    # Use defaults for missing title
    youtube_title = (payload.youtube_title or "").strip()
    if not youtube_title:
        youtube_title = f"{payload.product_name} Video Guide"

    job_payload = {
        "project_name": payload.project_name.strip(),
        "product_name": payload.product_name.strip(),
        "amazon_url": (payload.amazon_url or "").strip(),
        "product_brief": payload.product_brief.strip(),
        "video_mode": payload.video_mode,
        "ratio": payload.ratio,
        "clip_duration": payload.clip_duration,
        "clip_count": payload.clip_count,
        "resolution": payload.resolution,
        "youtube_title": youtube_title,
        "youtube_account_id": youtube_account_id,
        "youtube_description": (payload.youtube_description or "").strip(),
        "privacy": payload.privacy,
        "upload_to_youtube": 1 if payload.upload_to_youtube else 0,
        "stitch_final_video": 1 if payload.stitch_final_video else 0,
        "reference_image_urls_json": json.dumps(payload.reference_image_urls or [], ensure_ascii=False),
        "status": "queued",
        "current_step": "queued",
        "uploaded_images_note": "",
        "source": "api",
    }

    job_id = create_job(job_payload)
    create_clip_records(job_id=job_id, clip_count=payload.clip_count)

    # Enqueue via RQ task queue or local thread fallback
    from task_queue import enqueue_video_job
    enqueue_video_job(job_id)

    return {
        "status": "success",
        "job_id": job_id,
        "message": "Job successfully queued.",
        "details": {
            "project_name": payload.project_name,
            "product_name": payload.product_name,
            "youtube_account_id": youtube_account_id,
        }
    }


@app.post("/jobs/{job_id}/republish")
def api_republish_job(job_id: int):
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "failed":
        raise HTTPException(status_code=400, detail="Only failed jobs can be republished.")
    if int(job["upload_to_youtube"] or 0) != 1:
        raise HTTPException(status_code=400, detail="This job is not configured for YouTube upload.")
    if not (job["youtube_account_id"] or "").strip():
        raise HTTPException(status_code=400, detail="A YouTube account must be configured before republishing.")
    final_video_path = job["final_video_path"]
    if not final_video_path or not os.path.exists(final_video_path):
        raise HTTPException(status_code=400, detail="Final video file not found on disk. Cannot republish.")

    # Reset status
    update_job_fields(job_id, {
        "status": "uploading",
        "current_step": "Uploading to YouTube",
        "error_message": None
    })

    from task_queue import enqueue_republish_job
    enqueue_republish_job(job_id)
    return {"status": "success", "message": "Republish task started in the background."}

@app.post("/jobs/{job_id}/retry")
def api_retry_job(job_id: int):
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    from task_queue import enqueue_video_job, enqueue_storyboard_video_job

    if job["video_mode"] == "storyboard":
        update_job_fields(job_id, {
            "status": "queued",
            "current_step": "Restarting storyboard generation",
            "error_message": None,
            "workflow_stage": "videos_generating",
        })
        enqueue_storyboard_video_job(job_id)
    else:
        # Clear out clip statuses so it starts fresh
        from db import get_conn
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE clips SET status='queued', error_message=NULL WHERE job_id=?", (job_id,))
        conn.commit()
        conn.close()

        update_job_fields(job_id, {
            "status": "queued",
            "current_step": "queued",
            "error_message": None,
        })
        enqueue_video_job(job_id)

    return {"status": "success", "message": "Job successfully queued for retry."}


def republish_worker(job_id: int):
    try:
        from db import get_job_by_id, update_job_fields
        from youtube_core import upload_youtube
        from pathlib import Path

        job = get_job_by_id(job_id)
        if not job:
            return

        final_video_path = Path(job["final_video_path"])
        
        youtube_url = upload_youtube(
            video_path=final_video_path,
            title=job["youtube_title"],
            description=job["youtube_description"] or "",
            tags=[],
            privacy=job["privacy"],
            account_id=job["youtube_account_id"]
        )

        update_job_fields(job_id, {
            "status": "succeeded",
            "current_step": "completed",
            "youtube_url": youtube_url,
            "error_message": None
        })

        try:
            record_usage(
                job_id=job_id,
                stage="publishing",
                entity_type="job",
                entity_id=job_id,
                action="republish_youtube",
                model_name="youtube.videos.insert",
                total_tokens=0,
                estimated_cost_cny=0,
                status="succeeded",
            )
        except Exception:
            pass

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.exception("Republish worker failed for job %s", job_id)
        update_job_fields(job_id, {
            "status": "failed",
            "current_step": "failed",
            "error_message": str(e)
        })


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
    )
