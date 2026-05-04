import json
import os
import shutil
import uuid
from pathlib import Path
from typing import List

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from db import (
    create_clip_records,
    create_job,
    get_all_jobs,
    get_clip_rows_by_job_id,
    get_job_by_id,
    update_job_fields,
)
from youtube_core import list_youtube_accounts, create_oauth_flow, save_authorized_account
from video_core import ensure_runtime_dirs, run_video_job

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", str(BASE_DIR / "uploads"))).resolve()
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

ALLOWED_UPLOAD_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

app = FastAPI(title="ClipForge Tools")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", "clipforge-secret-key-12345"))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def resolve_lang(request: Request) -> str:
    lang = request.query_params.get("lang", "zh").lower()
    return lang if lang in {"en", "zh"} else "zh"


def build_common_context(request: Request) -> dict:
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


@app.on_event("startup")
def on_startup() -> None:
    ensure_runtime_dirs()


@app.get("/")
def index(request: Request):
    context = build_common_context(request)
    context.update(
        {
            "defaults": {
                "video_mode": "shorts",
                "ratio": "9:16",
                "clip_duration": 5,
                "clip_count": 2,
                "resolution": "720p",
                "privacy": "private",
            },
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
    youtube_account_id: str = Form(""),
    privacy: str = Form(...),
    reference_image_urls: str = Form(""),
    upload_to_youtube: bool = Form(False),
    stitch_final_video: bool = Form(True),
    reference_images: List[UploadFile] = File(default=[]),
):
    if upload_to_youtube and not youtube_account_id.strip():
        raise HTTPException(status_code=400, detail="YouTube account selection is required when upload is enabled.")
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

    background_tasks.add_task(run_video_job, job_id)
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
    return {"job": dict(job), "clips": [dict(row) for row in clips]}


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/youtube/authorize")
def youtube_authorize(request: Request):
    if not os.getenv("YOUTUBE_CLIENT_SECRET_JSON"):
        raise HTTPException(status_code=400, detail="YOUTUBE_CLIENT_SECRET_JSON not configured.")
    redirect_uri = str(request.url_for("youtube_oauth2callback"))
    proto = request.headers.get("x-forwarded-proto")
    if proto == "https" and redirect_uri.startswith("http://"):
        redirect_uri = redirect_uri.replace("http://", "https://", 1)
    
    flow = create_oauth_flow(redirect_uri)
    auth_url, state = flow.authorization_url(access_type='offline', prompt='consent')
    request.session['oauth_state'] = state
    return RedirectResponse(auth_url)


@app.get("/youtube/oauth2callback")
def youtube_oauth2callback(request: Request):
    state = request.session.get('oauth_state')
    redirect_uri = str(request.url_for("youtube_oauth2callback"))
    proto = request.headers.get("x-forwarded-proto")
    if proto == "https" and redirect_uri.startswith("http://"):
        redirect_uri = redirect_uri.replace("http://", "https://", 1)
        
    # Flow fetch_token requires HTTPS if not running locally, unless OAUTHLIB_INSECURE_TRANSPORT is set
    # The URL itself must have https scheme for OAuthLib to accept it
    auth_response_url = str(request.url)
    if proto == "https" and auth_response_url.startswith("http://"):
        auth_response_url = auth_response_url.replace("http://", "https://", 1)

    flow = create_oauth_flow(redirect_uri, state=state)
    try:
        flow.fetch_token(authorization_response=auth_response_url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to fetch token: {str(e)}")

    creds = flow.credentials
    save_authorized_account(creds)
    lang = resolve_lang(request)
    return RedirectResponse(url=f"/?lang={lang}", status_code=303)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
    )
