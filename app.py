import json
import os
import shutil
import uuid
from pathlib import Path
from typing import List

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from db import (
    create_clip_records,
    create_job,
    create_frame_image_version,
    create_storyboard_frames,
    create_template,
    delete_storyboard_frames_for_job,
    delete_template,
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

app = FastAPI(title="ClipForge Tools")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def resolve_lang(request: Request) -> str:
    lang = request.query_params.get("lang", "zh").lower()
    return lang if lang in {"en", "zh"} else "zh"


def build_common_context(request: Request) -> dict:
    ensure_runtime_dirs()
    lang = resolve_lang(request)
    youtube_accounts = list_youtube_accounts()
    return {
        "request": request,
        "lang": lang,
        "is_zh": lang == "zh",
        "lang_switch_en": str(request.url.include_query_params(lang="en")),
        "lang_switch_zh": str(request.url.include_query_params(lang="zh")),
        "youtube_accounts": youtube_accounts,
        "has_youtube_accounts": len(youtube_accounts) > 0,
    }


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


def generate_storyboard_prompts_job(job_id: int) -> None:
    job = get_job_by_id(job_id)
    if not job:
        return
    try:
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
        final_video_path = Path(job["final_video_path"]) if job["final_video_path"] else None
        if not final_video_path or not final_video_path.exists():
            raise RuntimeError("Final video is not ready for publishing.")
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


@app.on_event("startup")
def on_startup() -> None:
    ensure_runtime_dirs()


@app.get("/")
def index(request: Request):
    sample_job = {
        "project_name": "RV Hitch Shine Full Workflow",
        "product_name": "Cotton Polishing Wheel Kit",
        "amazon_url": "https://www.amazon.com/dp/B0TEST12345",
        "product_brief": (
            "6-inch cotton polishing wheel kit for bench grinder use. Designed for aluminum, brass, "
            "stainless steel, trailer hitch balls, and oxidized hardware. Show a full garage workflow "
            "from dull metal to reflective finish for Amazon buyers in garage DIY, automotive "
            "restoration, and RV maintenance."
        ),
        "video_mode": "long_video",
        "ratio": "9:16",
        "clip_duration": 10,
        "clip_count": 4,
        "resolution": "720p",
        "privacy": "unlisted",
        "youtube_title": "From Dull to Mirror Shine: 4-Step RV Hitch Polish Demo",
        "youtube_description": (
            "See a complete 4-step garage polishing workflow using a cotton polishing wheel kit. "
            "This demo covers hook, product closeup, setup, and final RV hitch shine result. "
            "Product link in description."
        ),
        "reference_image_urls": "\n".join(
            [
                "https://images.unsplash.com/photo-1503376780353-7e6692767b70",
                "https://images.unsplash.com/photo-1517524206127-48bbd363f3d7",
                "https://images.unsplash.com/photo-1486006920555-c77dcf18193c",
                "https://images.unsplash.com/photo-1486262715619-67b85e0b08d3",
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
                "Due to YouTube Data API restrictions, videos uploaded via videos.insert from projects "
                "created after July 28, 2020 and not yet audited may be forced to Private even if you select "
                "unlisted or public."
            ),
        }
    )
    return templates.TemplateResponse(
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
    return templates.TemplateResponse("jobs.html", context)


@app.get("/jobs/{job_id}")
def job_detail(request: Request, job_id: int):
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    clips = get_clip_rows_by_job_id(job_id)
    context = build_common_context(request)
    context.update({"job": job, "clips": clips})
    return templates.TemplateResponse("job.html", context)


@app.get("/jobs/{job_id}/status")
def job_status(job_id: int):
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    clips = get_clip_rows_by_job_id(job_id)
    queue = None
    try:
        from task_queue import get_job_status as get_rq_job_status

        queue = get_rq_job_status(job_id)
    except Exception as exc:
        queue = {"status": "unavailable", "error": str(exc)}
    return {"job": dict(job), "clips": [dict(row) for row in clips], "queue": queue}


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


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/v2")
def v2_index(request: Request):
    context = build_common_context(request)
    context.update(
        {
            "sample_idea": {
                "idea_title": "RV Hitch Polish Storyboard Flow",
                "project_name": "ClipForge 2.0 Sample",
                "product_name": "Cotton Polishing Wheel Kit",
                "simple_idea": "做一个更像教程型广告的 Shorts，先展示 RV hitch ball 氧化发乌，再展示抛光轮安装、接触金属、最后镜面反光效果。",
                "target_audience": "美国车库 DIY、RV 保养和汽修工具买家",
                "video_mode": "long_video",
                "ratio": "9:16",
                "clip_count": 4,
                "clip_duration": 10,
                "resolution": "720p",
                "style_preference": "真实美国车库、结构清晰、手部动作明确、强结果对比",
                "youtube_title": "4-Step RV Hitch Shine Workflow",
                "youtube_description": "Storyboard-first workflow demo for an RV hitch polishing product video.",
                "privacy": "unlisted",
            }
        }
    )
    return templates.TemplateResponse("v2_index.html", context)


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
    return templates.TemplateResponse("v2_jobs.html", context)


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
    return templates.TemplateResponse("v2_job.html", context)


@app.get("/v2/jobs/{job_id}/status")
def v2_job_status(job_id: int):
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    frames = [dict(frame) for frame in get_storyboard_frames(job_id)]
    clips = [dict(row) for row in get_clip_rows_by_job_id(job_id)]
    usage_events = [dict(row) for row in list_usage_events(job_id)]
    queue = None
    try:
        from task_queue import get_job_status as get_rq_job_status

        queue = get_rq_job_status(job_id)
    except Exception as exc:
        queue = {"status": "unavailable", "error": str(exc)}
    return {
        "job": dict(job),
        "frames": frames,
        "clips": clips,
        "usage_events": usage_events,
        "usage_totals": get_usage_totals_by_stage(job_id),
        "queue": queue,
    }


@app.post("/v2/jobs/{job_id}/regenerate-prompts")
def regenerate_v2_prompts(request: Request, job_id: int):
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
    return RedirectResponse(url=f"/v2/jobs/{frame['job_id']}?lang={resolve_lang(request)}", status_code=303)


@app.post("/v2/jobs/{job_id}/generate-images")
def generate_v2_images(request: Request, job_id: int):
    base_url = str(request.base_url).rstrip("/")
    from task_queue import enqueue_storyboard_images_job
    enqueue_storyboard_images_job(job_id, base_url)
    return RedirectResponse(url=f"/v2/jobs/{job_id}?lang={resolve_lang(request)}", status_code=303)


@app.post("/v2/frames/{frame_id}/regenerate-image")
def regenerate_v2_image(request: Request, frame_id: int):
    frame = get_storyboard_frame(frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="Storyboard frame not found")
    base_url = str(request.base_url).rstrip("/")
    from task_queue import enqueue_storyboard_images_job
    enqueue_storyboard_images_job(frame["job_id"], base_url, frame_id)
    return RedirectResponse(url=f"/v2/jobs/{frame['job_id']}?lang={resolve_lang(request)}", status_code=303)


@app.post("/v2/frames/{frame_id}/approve")
def approve_v2_frame(request: Request, frame_id: int):
    frame = get_storyboard_frame(frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="Storyboard frame not found")
    approved = 0 if int(frame["user_approved"] or 0) == 1 else 1
    update_storyboard_frame(frame_id, {"user_approved": approved})
    frames = get_storyboard_frames(frame["job_id"])
    next_stage = "images_approved" if frames and all(int(item["user_approved"] or 0) == 1 for item in frames) else "images_ready"
    update_job_fields(frame["job_id"], {"workflow_stage": next_stage, "current_step": "Storyboard review in progress"})
    return RedirectResponse(url=f"/v2/jobs/{frame['job_id']}?lang={resolve_lang(request)}", status_code=303)


@app.post("/v2/jobs/{job_id}/approve-all")
def approve_all_v2(request: Request, job_id: int):
    frames = get_storyboard_frames(job_id)
    if not frames:
        raise HTTPException(status_code=404, detail="No storyboard frames found")
    for frame in frames:
        update_storyboard_frame(frame["id"], {"user_approved": 1})
    update_job_fields(job_id, {"workflow_stage": "images_approved", "current_step": "All storyboard frames approved"})
    return RedirectResponse(url=f"/v2/jobs/{job_id}?lang={resolve_lang(request)}", status_code=303)


@app.post("/v2/jobs/{job_id}/launch-video")
def launch_v2_video(request: Request, job_id: int):
    from task_queue import enqueue_storyboard_video_job
    enqueue_storyboard_video_job(job_id)
    return RedirectResponse(url=f"/v2/jobs/{job_id}?lang={resolve_lang(request)}", status_code=303)


@app.post("/v2/jobs/{job_id}/frames/{frame_id}/set-version/{version_id}")
def set_v2_frame_version(request: Request, job_id: int, frame_id: int, version_id: int):
    version = get_frame_image_version(version_id)
    if not version:
        raise HTTPException(status_code=404, detail="Image version not found")
    set_current_frame_image_version(frame_id, version_id)
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
    return RedirectResponse(url=f"/v2/jobs/{job_id}?lang={resolve_lang(request)}", status_code=303)


@app.post("/v2/jobs/{job_id}/publish")
def publish_v2(request: Request, job_id: int):
    from task_queue import enqueue_publish_job
    enqueue_publish_job(job_id)
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
    )
