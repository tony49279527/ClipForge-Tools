import json
import os
import subprocess
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

import base64
import requests
from requests import HTTPError

try:
    from prompt_enhance import enhance_video_prompt
except ImportError:
    enhance_video_prompt = None

from db import (
    create_clip_records,
    delete_clips_for_job,
    get_current_frame_image_version,
    get_job_by_id,
    get_storyboard_frame,
    get_storyboard_frames,
    init_db,
    sum_clip_metrics,
    update_clip_by_job_and_index,
    update_job_fields,
)
from youtube_core import upload_youtube
from usage_core import record_usage, refresh_job_usage_totals

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = Path(os.getenv("UPLOADS_DIR", str(BASE_DIR / "uploads"))).resolve()
OUTPUTS_DIR = Path(os.getenv("OUTPUTS_DIR", str(BASE_DIR / "outputs"))).resolve()

COST_PER_MILLION_TOKENS_CNY = float(os.getenv("PRICE_PER_MILLION_TOKENS_CNY", "46"))
SEEDANCE_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
# Default to the known-working Seedance model for this account. Override with
# SEEDANCE_MODEL only when you've confirmed endpoint access for another model.
SEEDANCE_MODEL = os.getenv("SEEDANCE_MODEL", "doubao-seedance-2-0-260128")

STYLE_PREFIX = (
    "真实美国车库五金工具广告风格。场景为美国家庭车库、木质工作台、红色工具箱、墙面 pegboard、bench grinder、"
    "RV trailer hitch、铝合金零件。只出现戴黑色工作手套的成年男性双手，不出现人脸。真实摄影质感、浅景深、"
    "自然车库光、稳定镜头、专业可信。不要文字、不要 logo、不要卡通、不要科幻、不要人脸、不要危险动作、不要夸张火花。"
)


def ensure_runtime_dirs() -> None:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    init_db()


def estimate_cost_cny(total_tokens: int) -> float:
    return round(total_tokens / 1_000_000 * COST_PER_MILLION_TOKENS_CNY, 4)


def build_clip_prompts(job: Dict[str, Any]) -> List[Dict[str, Optional[str]]]:
    product_name = job["product_name"]
    product_brief = job["product_brief"]
    clip_count = job["clip_count"]
    reference_urls = json.loads(job["reference_image_urls_json"] or "[]")

    if not reference_urls:
        resolved_refs = [None] * clip_count
    elif len(reference_urls) >= clip_count:
        resolved_refs = reference_urls[:clip_count]
    else:
        resolved_refs = reference_urls + [reference_urls[-1]] * (clip_count - len(reference_urls))

    if clip_count == 1:
        beats = [
            f"完整的 before-after 产品演示。展示 {product_name} 如何解决问题，突出最终效果。产品卖点：{product_brief}"
        ]
    elif clip_count == 2:
        beats = [
            f"Hook + product closeup。快速建立问题场景并用特写突出 {product_name}。产品卖点：{product_brief}",
            f"Usage demo + before/after。展示 {product_name} 的实际使用过程和明显结果对比。产品卖点：{product_brief}",
        ]
    elif clip_count == 4:
        beats = [
            f"Hook。用强对比问题画面吸引用户，突出 {product_name} 的用途。产品卖点：{product_brief}",
            f"Product closeup。展示 {product_name} 细节、材质和关键结构。产品卖点：{product_brief}",
            f"Setup / preparation。展示上手准备动作和工具安装过程。产品卖点：{product_brief}",
            f"Usage demo / result。展示使用结果和 before-after 对比。产品卖点：{product_brief}",
        ]
    elif clip_count == 20:
        beats = [
            "Hook: dull metal to mirror shine",
            "Product hero closeup",
            "Package / wheel texture detail",
            "Bench grinder setup",
            "Safety preparation",
            "Polishing compound application",
            "First contact with metal",
            "Aluminum part polishing",
            "Brass part polishing",
            "RV hitch ball polishing",
            "Trailer bracket polishing",
            "Before-after comparison",
            "Garage DIY lifestyle",
            "Automotive restoration use case",
            "RV maintenance use case",
            "Material compatibility visual",
            "Durability / thick cotton layers",
            "Final shine montage",
            "Product on workbench hero shot",
            "CTA: check product link in description",
        ]
        beats = [f"{beat}。产品：{product_name}。产品卖点：{product_brief}" for beat in beats]
    else:
        beats = [
            f"Clip {index} for {product_name}。展示不同角度的产品价值与使用场景。产品卖点：{product_brief}"
            for index in range(1, clip_count + 1)
        ]

    prompts: List[Dict[str, Optional[str]]] = []
    for index, beat in enumerate(beats, start=1):
        prompt = f"{STYLE_PREFIX} 第 {index} 段：{beat}"
        prompts.append(
            {
                "clip_index": index,
                "prompt": prompt,
                "reference_image_url": resolved_refs[index - 1],
            }
        )
    return prompts





def build_seedance_content(prompt: str, reference_image_url: Optional[str] = None) -> List[Dict]:
    content = [{"type": "text", "text": prompt}]
    if reference_image_url:
        content.append({
            "type": "image_url",
            "image_url": {"url": reference_image_url},
            "role": "reference_image"
        })
    return content

def build_seedance_payload(
    prompt: str,
    ratio: str,
    duration: int,
    resolution: str,
    reference_image_url: Optional[str] = None,
) -> Dict:
    content = build_seedance_content(prompt, reference_image_url)
    return {
        "model": SEEDANCE_MODEL,
        "content": content,
        "generate_audio": True,
        "ratio": ratio,
        "duration": duration,
        "watermark": False,
    }


def create_seedance_task(
    prompt: str,
    ratio: str,
    duration: int,
    resolution: str,
    reference_image_url: Optional[str] = None,
) -> str:
    api_key = os.getenv("ARK_API_KEY")
    if not api_key:
        raise RuntimeError("ARK_API_KEY is not configured")

    payload = build_seedance_payload(prompt, ratio, duration, resolution, reference_image_url)
    endpoint = f"{SEEDANCE_BASE_URL}/contents/generations/tasks"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    proxies = {"http": None, "https": None}
    response = requests.post(endpoint, headers=headers, json=payload, timeout=60, proxies=proxies)
    try:
        response.raise_for_status()
    except HTTPError as exc:
        response_body = response.text.strip()
        # Backward-compatible retry if an account still expects nested `input`.
        if "MissingParameter" in response_body and "`content`" in response_body:
            fallback_payload = {
                "model": SEEDANCE_MODEL,
                "input": {
                    "ratio": ratio,
                    "duration": duration,
                    "resolution": resolution,
                    "content": build_seedance_content(prompt, reference_image_url),
                },
            }
            fallback_response = requests.post(
                endpoint,
                headers=headers,
                json=fallback_payload,
                timeout=60,
                proxies=proxies,
            )
            try:
                fallback_response.raise_for_status()
            except HTTPError as fallback_exc:
                raise RuntimeError(
                    "Seedance create task failed. "
                    f"status_code={fallback_response.status_code}, "
                    f"response={fallback_response.text.strip()}, "
                    f"request_payload={json.dumps(fallback_payload, ensure_ascii=False)}"
                ) from fallback_exc
            data = fallback_response.json()
            task_id = data.get("id") or data.get("task_id")
            if not task_id:
                raise RuntimeError(f"Seedance task id missing in fallback response: {data}")
            return task_id

        raise RuntimeError(
            "Seedance create task failed. "
            f"status_code={response.status_code}, "
            f"response={response_body}, "
            f"request_payload={json.dumps(payload, ensure_ascii=False)}"
        ) from exc
    data = response.json()
    task_id = data.get("id") or data.get("task_id")
    if not task_id:
        raise RuntimeError(f"Seedance task id missing in response: {data}")
    return task_id


def wait_seedance_task(task_id: str, poll_interval: int = 8, max_wait_seconds: int = 1800) -> Dict:
    api_key = os.getenv("ARK_API_KEY")
    if not api_key:
        raise RuntimeError("ARK_API_KEY is not configured")

    deadline = time.time() + max_wait_seconds
    proxies = {"http": None, "https": None}
    while time.time() < deadline:
        response = requests.get(
            f"{SEEDANCE_BASE_URL}/contents/generations/tasks/{task_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=60,
            proxies=proxies,
        )
        try:
            response.raise_for_status()
        except HTTPError as exc:
            response_body = response.text.strip()
            raise RuntimeError(
                "Seedance wait task failed. "
                f"task_id={task_id}, "
                f"status_code={response.status_code}, "
                f"response={response_body}"
            ) from exc
        data = response.json()
        status = str(data.get("status", "")).lower()
        if status in {"succeeded", "success", "completed"}:
            return data
        if status in {"failed", "error", "cancelled"}:
            raise RuntimeError(f"Seedance task failed: {data}")
        time.sleep(poll_interval)
    raise TimeoutError(f"Seedance task timed out after {max_wait_seconds} seconds: {task_id}")


def extract_video_url(task_result: Dict) -> str:
    content = task_result.get("content") or {}
    if isinstance(content, dict) and content.get("video_url"):
        return content["video_url"]

    output = task_result.get("output") or {}
    if isinstance(output, dict):
        if output.get("video_url"):
            return output["video_url"]
        videos = output.get("videos") or []
        if videos and isinstance(videos[0], dict) and videos[0].get("url"):
            return videos[0]["url"]
    if task_result.get("video_url"):
        return task_result["video_url"]
    raise RuntimeError(f"Could not extract video URL from Seedance result: {task_result}")


def extract_total_tokens(task_result: Dict) -> int:
    usage = task_result.get("usage") or {}
    return int(usage.get("total_tokens") or 0)


def download_video(video_url: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    proxies = {"http": None, "https": None}
    with requests.get(video_url, stream=True, timeout=300, proxies=proxies) as response:
        response.raise_for_status()
        with output_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
    return output_path


def concat_clips(clip_paths: List[Path], final_video_path: Path) -> Path:
    if not clip_paths:
        raise ValueError("No clip paths provided for concatenation")

    clips_txt = final_video_path.parent / "clips.txt"
    with clips_txt.open("w", encoding="utf-8") as handle:
        for clip_path in clip_paths:
            handle.write(f"file '{clip_path.resolve()}'\n")

    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(clips_txt),
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(final_video_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg concat failed: {result.stderr}")
    return final_video_path


def upload_to_object_storage(file_path: Path) -> Optional[str]:
    del file_path
    return None


def build_storyboard_video_specs(job: Dict[str, Any]) -> List[Dict[str, Any]]:
    frames = get_storyboard_frames(job["id"])
    approved_frames = [frame for frame in frames if int(frame["selected_for_video"] or 0) == 1]
    if not approved_frames:
        raise RuntimeError("No storyboard frames are selected for video generation.")

    missing_approvals = [frame["clip_index"] for frame in approved_frames if int(frame["user_approved"] or 0) != 1]
    if missing_approvals:
        raise RuntimeError(f"Storyboard frames not approved yet: {missing_approvals}")

    specs: List[Dict[str, Any]] = []
    for frame in approved_frames:
        current_version = get_current_frame_image_version(frame["id"])
        reference_image_url = None
        if current_version:
            reference_image_url = current_version["image_remote_url"] or frame["image_remote_url"]
        if not reference_image_url:
            # Fallback: read local file and encode as base64 data URI so Seedance can consume it
            local_path_str = (current_version["image_local_path"] if current_version else None) or frame.get("image_local_path")
            if local_path_str:
                local_path = Path(local_path_str)
                if local_path.exists():
                    with local_path.open("rb") as f:
                        image_bytes = f.read()
                    ext = local_path.suffix.lstrip(".")
                    if ext not in ("png", "jpg", "jpeg", "webp"):
                        ext = "png"
                    reference_image_url = f"data:image/{ext};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        if not reference_image_url:
            raise RuntimeError(
                f"Storyboard frame {frame['clip_index']} does not have a public image URL or local image file for Seedance."
            )
        specs.append(
            {
                "clip_index": frame["clip_index"],
                "frame_id": frame["id"],
                "prompt": frame["prompt_en"] or frame["prompt_zh"],
                "reference_image_url": reference_image_url,
                "image_version_id": current_version["id"] if current_version else None,
            }
        )
    return specs


def run_video_job(job_id: int) -> None:
    ensure_runtime_dirs()
    job = get_job_by_id(job_id)
    if not job:
        return

    job_output_dir = OUTPUTS_DIR / str(job_id)
    clips_dir = job_output_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    current_clip_index: Optional[int] = None
    try:
        update_job_fields(job_id, {"status": "running", "current_step": "preparing prompts"})
        prompt_specs = build_clip_prompts(job)

        clip_paths: List[Path] = []
        for spec in prompt_specs:
            clip_index = spec["clip_index"]
            current_clip_index = clip_index
            update_job_fields(
                job_id,
                {
                    "status": "generating_clips",
                    "current_step": f"Generating clip {clip_index}/{job['clip_count']}",
                },
            )
            update_clip_by_job_and_index(
                job_id,
                clip_index,
                {
                    "prompt": spec["prompt"],
                    "reference_image_url": spec["reference_image_url"],
                    "status": "running",
                },
            )

            try:
                # --- Step 1: Create Task with Image-Error Retry ---
                try:
                    seedance_task_id = create_seedance_task(
                        prompt=spec["prompt"],
                        ratio=job["ratio"],
                        duration=job["clip_duration"],
                        resolution=job["resolution"],
                        reference_image_url=spec["reference_image_url"],
                    )
                except Exception as e:
                    # Retry WITHOUT reference image if:
                    # 1. Sensitive content detected
                    # 2. Invalid parameter (like image too large)
                    error_msg = str(e)
                    is_image_error = "InputImageSensitiveContentDetected" in error_msg or \
                                     ("InvalidParameter" in error_msg and "image" in error_msg.lower())
                    
                    if is_image_error and spec["reference_image_url"]:
                        print(f"Clip {clip_index}: Image issue detected ({error_msg}). Retrying WITHOUT reference image...")
                        seedance_task_id = create_seedance_task(
                            prompt=spec["prompt"],
                            ratio=job["ratio"],
                            duration=job["clip_duration"],
                            resolution=job["resolution"],
                            reference_image_url=None, # Drop the problematic image
                        )
                    else:
                        raise e

                update_clip_by_job_and_index(
                    job_id,
                    clip_index,
                    {"seedance_task_id": seedance_task_id, "status": "submitted"},
                )

                # --- Step 2: Wait and Download ---
                task_result = wait_seedance_task(seedance_task_id)
                video_url = extract_video_url(task_result)
                total_tokens = extract_total_tokens(task_result)
                cost = estimate_cost_cny(total_tokens)
                output_path = clips_dir / f"clip_{clip_index:02d}.mp4"
                download_video(video_url, output_path)
                clip_paths.append(output_path)

                update_clip_by_job_and_index(
                    job_id,
                    clip_index,
                    {
                        "status": "succeeded",
                        "local_path": str(output_path),
                        "tokens": total_tokens,
                        "estimated_cost_cny": cost,
                    },
                )
                record_usage(
                    job_id=job_id,
                    stage="video_generation",
                    entity_type="clip",
                    entity_id=clip_index,
                    action="generate_video",
                    model_name=SEEDANCE_MODEL,
                    total_tokens=total_tokens,
                    estimated_cost_cny=cost,
                    raw_usage=task_result.get("usage") or {},
                )
            except Exception as clip_exc:
                print(f"Clip {clip_index} failed: {clip_exc}")
                update_clip_by_job_and_index(
                    job_id,
                    clip_index,
                    {"status": "failed", "error_message": str(clip_exc)},
                )
                # Continue to next clip...

            metrics = sum_clip_metrics(job_id)
            update_job_fields(
                job_id,
                {
                    "total_tokens": metrics["total_tokens"],
                    "estimated_cost_cny": round(metrics["estimated_cost_cny"], 4),
                },
            )

        final_video_path = None
        if int(job["stitch_final_video"]) == 1:
            update_job_fields(job_id, {"status": "stitching", "current_step": "Concatenating clips"})
            final_video_path = concat_clips(clip_paths, job_output_dir / "final_video.mp4")
            update_job_fields(job_id, {"final_video_path": str(final_video_path)})
        elif clip_paths:
            final_video_path = clip_paths[0]
            update_job_fields(job_id, {"final_video_path": str(final_video_path)})

        youtube_url = None
        if int(job["upload_to_youtube"]) == 1 and final_video_path:
            update_job_fields(job_id, {"status": "uploading", "current_step": "Uploading to YouTube"})
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
            update_job_fields(job_id, {"youtube_url": youtube_url})

        update_job_fields(job_id, {"status": "succeeded", "current_step": "Completed"})
    except Exception as exc:
        if current_clip_index is not None:
            update_clip_by_job_and_index(
                job_id,
                current_clip_index,
                {
                    "status": "failed",
                    "error_message": str(exc),
                },
            )
        update_job_fields(
            job_id,
            {
                "status": "failed",
                "current_step": "Failed",
                "error_message": str(exc),
            },
        )


def run_storyboard_video_job(job_id: int) -> None:
    ensure_runtime_dirs()
    job = get_job_by_id(job_id)
    if not job:
        return

    job_output_dir = OUTPUTS_DIR / str(job_id)
    clips_dir = job_output_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    current_clip_index: Optional[int] = None

    try:
        update_job_fields(
            job_id,
            {
                "status": "running",
                "workflow_stage": "videos_generating",
                "current_step": "Preparing approved storyboard frames",
                "error_message": None,
            },
        )
        specs = build_storyboard_video_specs(job)
        delete_clips_for_job(job_id)
        create_clip_records(job_id=job_id, clip_count=len(specs))
        clip_paths: List[Path] = []

        for spec in specs:
            clip_index = spec["clip_index"]
            current_clip_index = clip_index
            update_job_fields(
                job_id,
                {
                    "status": "generating_clips",
                    "workflow_stage": "videos_generating",
                    "current_step": f"Generating clip {clip_index}/{len(specs)} from storyboard",
                },
            )
            update_clip_by_job_and_index(
                job_id,
                clip_index,
                {
                    "frame_id": spec["frame_id"],
                    "video_prompt_en": spec["prompt"],
                    "reference_image_url": spec["reference_image_url"],
                    "selected_image_version_id": spec["image_version_id"],
                    "status": "running",
                },
            )
            # Enhance prompt if the module is available
            video_prompt = spec["prompt"]
            if enhance_video_prompt is not None:
                frame_meta = get_storyboard_frame(spec["frame_id"])
                video_prompt = enhance_video_prompt(
                    original_prompt=spec["prompt"],
                    product_name=job.get("product_name", ""),
                    scene_role=frame_meta["scene_role"] if frame_meta else "scene",
                    clip_duration=job.get("clip_duration", 10),
                    ratio=job.get("ratio", "9:16"),
                )
            seedance_task_id = create_seedance_task(
                prompt=video_prompt,
                ratio=job["ratio"],
                duration=job["clip_duration"],
                resolution=job["resolution"],
                reference_image_url=spec["reference_image_url"],
            )
            update_clip_by_job_and_index(job_id, clip_index, {"seedance_task_id": seedance_task_id, "status": "submitted"})
            task_result = wait_seedance_task(seedance_task_id)
            video_url = extract_video_url(task_result)
            total_tokens = extract_total_tokens(task_result)
            cost = estimate_cost_cny(total_tokens)
            output_path = clips_dir / f"clip_{clip_index:02d}.mp4"
            download_video(video_url, output_path)
            clip_paths.append(output_path)
            update_clip_by_job_and_index(
                job_id,
                clip_index,
                {
                    "status": "succeeded",
                    "local_path": str(output_path),
                    "tokens": total_tokens,
                    "estimated_cost_cny": cost,
                },
            )
            record_usage(
                job_id=job_id,
                stage="video_generation",
                entity_type="clip",
                entity_id=clip_index,
                action="generate_video",
                model_name=SEEDANCE_MODEL,
                total_tokens=total_tokens,
                estimated_cost_cny=cost,
                raw_usage=task_result.get("usage") or {},
            )

        final_video_path = None
        if int(job["stitch_final_video"]) == 1:
            update_job_fields(job_id, {"status": "stitching", "current_step": "Concatenating clips"})
            final_video_path = concat_clips(clip_paths, job_output_dir / "final_video.mp4")
            update_job_fields(job_id, {"final_video_path": str(final_video_path)})
        elif clip_paths:
            final_video_path = clip_paths[0]
            update_job_fields(job_id, {"final_video_path": str(final_video_path)})

        refresh_job_usage_totals(job_id)
        update_job_fields(job_id, {"status": "succeeded", "workflow_stage": "videos_ready", "current_step": "Video generation completed"})
    except Exception as exc:
        if current_clip_index is not None:
            update_clip_by_job_and_index(job_id, current_clip_index, {"status": "failed", "error_message": str(exc)})
        update_job_fields(
            job_id,
            {
                "status": "failed",
                "workflow_stage": "failed",
                "current_step": "Failed",
                "error_message": str(exc),
            },
        )


# ======================================================================
# Parallel clip generation (used by RQ task queue)
# ======================================================================

MAX_CLIP_WORKERS = int(os.getenv("MAX_CLIP_WORKERS", "4"))


def _is_reference_image_retryable(error_msg: str) -> bool:
    lowered_error = error_msg.lower()
    return "InputImageSensitiveContentDetected" in error_msg or (
        "InvalidParameter" in error_msg and "image" in lowered_error
    )


def _ensure_at_least_one_successful_clip(job_id: int, clip_paths: List[Path], expected_count: int) -> None:
    if clip_paths:
        return
    raise RuntimeError(
        f"All {expected_count} clip generations failed for job {job_id}."
    )


def _generate_single_clip(
    job_id: int,
    spec: Dict[str, Any],
    clips_dir: Path,
    job: Dict[str, Any],
) -> Optional[Path]:
    """Generate a single clip: create Seedance task, wait, download.
    Returns the clip path on success, None on failure.
    """
    clip_index = spec["clip_index"]
    try:
        update_clip_by_job_and_index(
            job_id,
            clip_index,
            {
                "prompt": spec["prompt"],
                "reference_image_url": spec.get("reference_image_url"),
                "status": "running",
            },
        )

        # --- Step 1: Create Task with Image-Error Retry ---
        try:
            seedance_task_id = create_seedance_task(
                prompt=spec["prompt"],
                ratio=job["ratio"],
                duration=job["clip_duration"],
                resolution=job["resolution"],
                reference_image_url=spec.get("reference_image_url"),
            )
        except Exception as e:
            error_msg = str(e)
            is_image_error = _is_reference_image_retryable(error_msg)
            if is_image_error and spec.get("reference_image_url"):
                print(f"Clip {clip_index}: Image issue, retrying WITHOUT reference image...")
                seedance_task_id = create_seedance_task(
                    prompt=spec["prompt"],
                    ratio=job["ratio"],
                    duration=job["clip_duration"],
                    resolution=job["resolution"],
                    reference_image_url=None,
                )
            else:
                raise e

        update_clip_by_job_and_index(
            job_id, clip_index,
            {"seedance_task_id": seedance_task_id, "status": "submitted"},
        )

        # --- Step 2: Wait and Download ---
        task_result = wait_seedance_task(seedance_task_id)
        video_url = extract_video_url(task_result)
        total_tokens = extract_total_tokens(task_result)
        cost = estimate_cost_cny(total_tokens)
        output_path = clips_dir / f"clip_{clip_index:02d}.mp4"
        download_video(video_url, output_path)

        update_clip_by_job_and_index(
            job_id,
            clip_index,
            {
                "status": "succeeded",
                "local_path": str(output_path),
                "tokens": total_tokens,
                "estimated_cost_cny": cost,
            },
        )
        record_usage(
            job_id=job_id,
            stage="video_generation",
            entity_type="clip",
            entity_id=clip_index,
            action="generate_video",
            model_name=SEEDANCE_MODEL,
            total_tokens=total_tokens,
            estimated_cost_cny=cost,
            raw_usage=task_result.get("usage") or {},
        )
        return output_path
    except Exception as clip_exc:
        print(f"Clip {clip_index} failed: {clip_exc}")
        traceback.print_exc()
        update_clip_by_job_and_index(
            job_id,
            clip_index,
            {"status": "failed", "error_message": str(clip_exc)},
        )
        return None


def run_video_job_parallel(job_id: int) -> Dict[str, Any]:
    """Run a 1.0 video job with parallel clip generation using ThreadPoolExecutor."""
    job = get_job_by_id(job_id)
    if not job:
        return {"job_id": job_id, "status": "not_found"}

    job_output_dir = OUTPUTS_DIR / str(job_id)
    clips_dir = job_output_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    try:
        update_job_fields(job_id, {"status": "running", "current_step": "preparing prompts"})
        prompt_specs = build_clip_prompts(job)

        # --- Parallel clip generation ---
        max_workers = min(MAX_CLIP_WORKERS, len(prompt_specs))
        update_job_fields(
            job_id,
            {
                "status": "generating_clips",
                "current_step": f"Generating {len(prompt_specs)} clips in parallel (max {max_workers} workers)",
            },
        )

        clip_paths: List[Path] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    _generate_single_clip, job_id, spec, clips_dir, job
                ): spec["clip_index"]
                for spec in prompt_specs
            }
            for future in as_completed(future_map):
                clip_index = future_map[future]
                try:
                    result = future.result()
                    if result:
                        clip_paths.append(result)
                except Exception as exc:
                    print(f"Unexpected error in clip {clip_index}: {exc}")

        # Sort clip paths by clip_index for consistent stitching order
        _ensure_at_least_one_successful_clip(job_id, clip_paths, len(prompt_specs))
        clip_paths.sort(key=lambda p: int(p.stem.split("_")[1]))

        # Update aggregated metrics
        metrics = sum_clip_metrics(job_id)
        update_job_fields(
            job_id,
            {
                "total_tokens": metrics["total_tokens"],
                "estimated_cost_cny": round(metrics["estimated_cost_cny"], 4),
            },
        )

        # --- Stitching ---
        final_video_path = None
        if int(job["stitch_final_video"]) == 1 and clip_paths:
            update_job_fields(job_id, {"status": "stitching", "current_step": "Concatenating clips"})
            final_video_path = concat_clips(clip_paths, job_output_dir / "final_video.mp4")
            update_job_fields(job_id, {"final_video_path": str(final_video_path)})
        elif clip_paths:
            final_video_path = clip_paths[0]
            update_job_fields(job_id, {"final_video_path": str(final_video_path)})

        # --- YouTube Upload ---
        youtube_url = None
        if int(job["upload_to_youtube"]) == 1 and final_video_path:
            update_job_fields(job_id, {"status": "uploading", "current_step": "Uploading to YouTube"})
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
            update_job_fields(job_id, {"youtube_url": youtube_url})

        update_job_fields(job_id, {"status": "succeeded", "current_step": "Completed"})
        return {"job_id": job_id, "status": "succeeded", "clips": len(clip_paths), "final_video": str(final_video_path) if final_video_path else None}

    except Exception as exc:
        print(f"Job {job_id} failed: {exc}")
        traceback.print_exc()
        update_job_fields(
            job_id,
            {
                "status": "failed",
                "current_step": "Failed",
                "error_message": str(exc),
            },
        )
        return {"job_id": job_id, "status": "failed", "error": str(exc)}


def _generate_single_storyboard_clip(
    job_id: int,
    spec: Dict[str, Any],
    clips_dir: Path,
    job: Dict[str, Any],
) -> Optional[Path]:
    """Generate a single storyboard clip (with optional prompt enhancement)."""
    clip_index = spec["clip_index"]
    try:
        update_clip_by_job_and_index(
            job_id,
            clip_index,
            {
                "frame_id": spec["frame_id"],
                "video_prompt_en": spec["prompt"],
                "reference_image_url": spec["reference_image_url"],
                "selected_image_version_id": spec.get("image_version_id"),
                "status": "running",
            },
        )

        video_prompt = spec["prompt"]
        if enhance_video_prompt is not None:
            frame_meta = get_storyboard_frame(spec["frame_id"])
            video_prompt = enhance_video_prompt(
                original_prompt=spec["prompt"],
                product_name=job.get("product_name", ""),
                scene_role=frame_meta["scene_role"] if frame_meta else "scene",
                clip_duration=job.get("clip_duration", 10),
                ratio=job.get("ratio", "9:16"),
            )

        try:
            seedance_task_id = create_seedance_task(
                prompt=video_prompt,
                ratio=job["ratio"],
                duration=job["clip_duration"],
                resolution=job["resolution"],
                reference_image_url=spec["reference_image_url"],
            )
        except Exception as exc:
            error_msg = str(exc)
            if _is_reference_image_retryable(error_msg) and spec.get("reference_image_url"):
                print(f"Storyboard clip {clip_index}: Image issue, retrying WITHOUT reference image...")
                seedance_task_id = create_seedance_task(
                    prompt=video_prompt,
                    ratio=job["ratio"],
                    duration=job["clip_duration"],
                    resolution=job["resolution"],
                    reference_image_url=None,
                )
            else:
                raise
        update_clip_by_job_and_index(
            job_id, clip_index,
            {"seedance_task_id": seedance_task_id, "status": "submitted"},
        )

        task_result = wait_seedance_task(seedance_task_id)
        video_url = extract_video_url(task_result)
        total_tokens = extract_total_tokens(task_result)
        cost = estimate_cost_cny(total_tokens)
        output_path = clips_dir / f"clip_{clip_index:02d}.mp4"
        download_video(video_url, output_path)

        update_clip_by_job_and_index(
            job_id,
            clip_index,
            {
                "status": "succeeded",
                "local_path": str(output_path),
                "tokens": total_tokens,
                "estimated_cost_cny": cost,
            },
        )
        record_usage(
            job_id=job_id,
            stage="video_generation",
            entity_type="clip",
            entity_id=clip_index,
            action="generate_video",
            model_name=SEEDANCE_MODEL,
            total_tokens=total_tokens,
            estimated_cost_cny=cost,
            raw_usage=task_result.get("usage") or {},
        )
        return output_path
    except Exception as clip_exc:
        print(f"Storyboard clip {clip_index} failed: {clip_exc}")
        traceback.print_exc()
        update_clip_by_job_and_index(
            job_id,
            clip_index,
            {"status": "failed", "error_message": str(clip_exc)},
        )
        return None


def run_storyboard_video_job_parallel(job_id: int) -> Dict[str, Any]:
    """Run a 2.0 storyboard video job with parallel clip generation."""
    job = get_job_by_id(job_id)
    if not job:
        return {"job_id": job_id, "status": "not_found"}

    job_output_dir = OUTPUTS_DIR / str(job_id)
    clips_dir = job_output_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    try:
        update_job_fields(
            job_id,
            {
                "status": "running",
                "workflow_stage": "videos_generating",
                "current_step": "Preparing approved storyboard frames",
                "error_message": None,
            },
        )
        specs = build_storyboard_video_specs(job)
        delete_clips_for_job(job_id)
        create_clip_records(job_id=job_id, clip_count=len(specs))

        # --- Parallel clip generation ---
        max_workers = min(MAX_CLIP_WORKERS, len(specs))
        update_job_fields(
            job_id,
            {
                "status": "generating_clips",
                "workflow_stage": "videos_generating",
                "current_step": f"Generating {len(specs)} storyboard clips in parallel (max {max_workers} workers)",
            },
        )

        clip_paths: List[Path] = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    _generate_single_storyboard_clip, job_id, spec, clips_dir, job
                ): spec["clip_index"]
                for spec in specs
            }
            for future in as_completed(future_map):
                clip_index = future_map[future]
                try:
                    result = future.result()
                    if result:
                        clip_paths.append(result)
                except Exception as exc:
                    print(f"Unexpected error in storyboard clip {clip_index}: {exc}")

        _ensure_at_least_one_successful_clip(job_id, clip_paths, len(specs))
        clip_paths.sort(key=lambda p: int(p.stem.split("_")[1]))

        # --- Stitching ---
        final_video_path = None
        if int(job["stitch_final_video"]) == 1 and clip_paths:
            update_job_fields(job_id, {"status": "stitching", "workflow_stage": "videos_generating", "current_step": "Concatenating clips"})
            final_video_path = concat_clips(clip_paths, job_output_dir / "final_video.mp4")
            update_job_fields(job_id, {"final_video_path": str(final_video_path)})
        elif clip_paths:
            final_video_path = clip_paths[0]
            update_job_fields(job_id, {"final_video_path": str(final_video_path)})

        refresh_job_usage_totals(job_id)
        update_job_fields(
            job_id,
            {
                "status": "succeeded",
                "workflow_stage": "videos_ready",
                "current_step": "Video generation completed",
            },
        )
        return {"job_id": job_id, "status": "succeeded", "clips": len(clip_paths), "final_video": str(final_video_path) if final_video_path else None}

    except Exception as exc:
        print(f"Storyboard job {job_id} failed: {exc}")
        traceback.print_exc()
        update_job_fields(
            job_id,
            {
                "status": "failed",
                "workflow_stage": "failed",
                "current_step": "Failed",
                "error_message": str(exc),
            },
        )
        return {"job_id": job_id, "status": "failed", "error": str(exc)}
