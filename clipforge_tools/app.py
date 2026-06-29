import json
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from db import (
    create_clip_records,
    create_job,
    get_all_jobs,
    get_clip_rows_by_job_id,
    get_job_by_id,
)
from video_core import ensure_runtime_dirs, run_video_job

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

ALLOWED_UPLOAD_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_runtime_dirs()
    yield


app = FastAPI(title="ClipForge Tools", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


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


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "request": request,
            "defaults": {
                "video_mode": "shorts",
                "ratio": "9:16",
                "clip_duration": 5,
                "clip_count": 2,
                "resolution": "720p",
                "privacy": "private",
            },
        },
    )


@app.post("/jobs")
async def create_job_view(
    request: Request,
    background_tasks: BackgroundTasks,
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
    privacy: str = Form(...),
    reference_image_urls: str = Form(""),
    upload_to_youtube: bool = Form(False),
    final_video: bool = Form(True),
    reference_images: List[UploadFile] = File(default=[]),
):
    del request
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
        "youtube_description": youtube_description.strip(),
        "privacy": privacy,
        "reference_image_urls_json": json.dumps(raw_urls, ensure_ascii=False),
        "status": "queued",
        "current_step": "queued",
        "upload_to_youtube": 1 if upload_to_youtube else 0,
        "final_video": 1 if final_video else 0,
        "uploaded_images_note": "",
    }
    job_id = create_job(job_payload)
    create_clip_records(job_id=job_id, clip_count=clip_count)

    saved_uploads = save_uploaded_files(job_id, reference_images)
    if saved_uploads:
        note = (
            "Local uploaded images were saved on the server, but Seedance currently needs public image URLs. "
            "Configure object storage later to turn uploaded images into reference_image URLs."
        )
        from db import update_job_fields

        update_job_fields(job_id, {"uploaded_images_note": note})

    background_tasks.add_task(run_video_job, job_id)
    return RedirectResponse(url=f"/jobs/{job_id}", status_code=303)


@app.get("/jobs")
def jobs(request: Request):
    rows = get_all_jobs()
    return templates.TemplateResponse(request, "jobs.html", {"jobs": rows})


@app.get("/jobs/{job_id}")
def job_detail(request: Request, job_id: int):
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    clips = get_clip_rows_by_job_id(job_id)
    return templates.TemplateResponse(request, "job.html", {"job": job, "clips": clips})


@app.get("/jobs/{job_id}/status")
def job_status(job_id: int):
    job = get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    clips = get_clip_rows_by_job_id(job_id)
    return {"job": dict(job), "clips": [dict(row) for row in clips]}
