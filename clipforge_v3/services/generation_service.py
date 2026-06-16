from __future__ import annotations

import json
import os
import random
import hashlib
import subprocess
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import requests

from db import utc_now

from clipforge_v3.compiler.preflight import run_preflight
from clipforge_v3.compiler.prompt_compiler import (
    CompilerInput,
    anti_slop_pass,
    build_director_prompt,
    compress_to_budget,
    detect_conflicts,
    enforce_single_camera_move,
    enforce_single_visible_beat,
    inject_continuity_anchors,
    inject_product_constraints,
    inject_reference_role_map,
    normalize_input,
    select_mode_template,
    validate_final_prompt,
)
from clipforge_v3.providers.config import SEEDANCE_GENERATE_AUDIO
from clipforge_v3.providers.seedance_ark import get_provider
from clipforge_v3.repositories import project_repository, shot_repository, take_repository
from clipforge_v3.schemas.generation import PromptCompileResult
from clipforge_v3.services.asset_service import list_assets
from clipforge_v3.services.continuity_service import build_continuity_context, record_continuity_from_take
from clipforge_v3.services.observability_service import sanitize
from clipforge_v3.services.product_truth_service import get_latest_product_truth
from clipforge_v3.services.provider_asset_resolver import resolve_provider_references
from clipforge_v3.services.scheduling_service import compute_schedule_state
from clipforge_v3.services.shot_service import list_shots


OUTPUTS_DIR = Path(os.getenv("OUTPUTS_DIR", "outputs")).resolve()
REAL_API_CONFIRM_TEXT = "I confirm this is a real paid Seedance generation."
REAL_API_TERMINAL_CONFIRM = "I_UNDERSTAND_THIS_COSTS_MONEY"
SUBMISSION_TERMINAL_STATES = {"succeeded", "failed", "cancelled", "poll_timeout", "unknown_submission_state"}
SUBMISSION_MANUAL_RECONCILIATION_STATES = {"unknown_submission_state"}
SUBMISSION_PROVIDER_SUBMIT_STATES = {"reserved", "queued"}
SUBMISSION_POLLABLE_STATES = {"submitted", "polling", "downloading"}


def _decode_prompt_version(row: dict) -> dict:
    for field in ("role_map_json", "compiler_warnings_json", "validation_result_json", "removed_items_json", "provider_payload_json"):
        default = "{}" if field in {"role_map_json", "validation_result_json", "provider_payload_json"} else "[]"
        row[field] = json.loads(row[field] or default)
    row["allow_submit"] = bool(row.get("allow_submit"))
    row["locked_by_user"] = bool(row.get("locked_by_user"))
    return row


def _decode_take(row: dict) -> dict:
    for field in ("generation_settings_json", "source_asset_ids_json", "qc_frame_paths_json", "review_summary_json"):
        default = "{}" if field in {"generation_settings_json", "review_summary_json"} else "[]"
        row[field] = json.loads(row[field] or default)
    row["selected_by_user"] = bool(row.get("selected_by_user"))
    row["uncontrolled_revision"] = bool(row.get("uncontrolled_revision"))
    row["deleted_local_file"] = bool(row.get("deleted_local_file"))
    return row


def get_video_provider_mode() -> str:
    provider = os.getenv("V3_VIDEO_PROVIDER", "mock").strip().lower()
    return provider if provider in {"mock", "ark"} else "mock"


def real_api_enabled() -> bool:
    return os.getenv("V3_REAL_API_ENABLED", "false").strip().lower() == "true"


def requires_manual_reconciliation(submission: dict) -> bool:
    status = submission.get("submission_status")
    if status in SUBMISSION_MANUAL_RECONCILIATION_STATES:
        return True
    return status == "submitting" and not submission.get("provider_task_id")


def is_terminal_submission_state(submission: dict) -> bool:
    return submission.get("submission_status") in SUBMISSION_TERMINAL_STATES


def can_call_provider_submit(submission: dict) -> bool:
    return (
        not submission.get("provider_task_id")
        and bool(submission.get("budget_approved_at"))
        and submission.get("submission_status") in SUBMISSION_PROVIDER_SUBMIT_STATES
    )


def can_poll_existing_task(submission: dict) -> bool:
    return bool(submission.get("provider_task_id")) and not is_terminal_submission_state(submission)


def can_enqueue_submission(submission: dict) -> bool:
    if requires_manual_reconciliation(submission):
        return False
    if submission.get("take_id") and submission.get("submission_status") == "succeeded":
        return False
    if can_poll_existing_task(submission):
        return True
    return can_call_provider_submit(submission)


def _manual_reconciliation_response(submission: dict, *, reason: str = "manual_reconciliation_required") -> dict:
    possible_charge = reason != "budget_approval_required"
    message = (
        "Submission is missing budget approval and cannot be submitted automatically."
        if reason == "budget_approval_required"
        else "Submission may already have produced a paid provider task. Automatic retry is disabled; inspect the Ark console and reconcile manually."
    )
    error_json = dict(submission.get("error_json") or {})
    if error_json.get("code") != reason:
        error_json = error_json | {"code": reason, "message": message, "possible_charge": possible_charge, "auto_retry_disabled": True}
        take_repository.update_generation_submission(submission["id"], {"error_json": error_json})
        submission = take_repository.get_generation_submission(submission["id"]) or submission
    return {
        "submission_id": submission["id"],
        "status": submission.get("submission_status"),
        "manual_reconciliation_required": True,
        "possible_charge": possible_charge,
        "auto_retry_disabled": True,
        "reason": reason,
        "message": message,
    }


def _canonical_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _reference_asset_version(prompt_version: dict) -> list:
    return sorted(
        [
            {
                "asset_id": item.get("asset_id"),
                "role": item.get("primary_role"),
                "must_transfer": item.get("must_transfer", []),
                "must_not_transfer": item.get("must_not_transfer", []),
            }
            for item in prompt_version.get("role_map_json", {}).get("assets", [])
        ],
        key=lambda item: (str(item.get("asset_id")), str(item.get("role"))),
    )


def build_idempotency_key(*, project_id: int, shot_id: int, prompt_version_id: int, tier: str, provider: str, prompt_version: dict) -> str:
    payload = {
        "project_id": project_id,
        "shot_id": shot_id,
        "prompt_version_id": prompt_version_id,
        "generation_tier": tier,
        "provider": provider,
        "resolution": prompt_version["provider_payload_json"].get("resolution"),
        "duration": prompt_version["provider_payload_json"].get("duration"),
        "reference_asset_version": _reference_asset_version(prompt_version),
    }
    return _sha256_text(_canonical_json(payload))


def build_paid_confirmation(*, project_id: int, shot_id: int, prompt_version_id: int, tier: str) -> dict:
    prompt_version = _decode_prompt_version(dict(shot_repository.get_prompt_version(prompt_version_id)))
    shot = next(item for item in list_shots(project_id) if item["id"] == shot_id)
    project = dict(project_repository.get_project(project_id))
    provider_name = get_video_provider_mode()
    idempotency_key = build_idempotency_key(
        project_id=project_id,
        shot_id=shot_id,
        prompt_version_id=prompt_version_id,
        tier=tier,
        provider=provider_name,
        prompt_version=prompt_version,
    )
    provider = get_provider()
    estimate = provider.estimate_cost(
        duration=prompt_version["provider_payload_json"].get("duration", shot["duration"]),
        resolution=prompt_version["provider_payload_json"].get("resolution", project["resolution"]),
    )
    return {
        "provider": provider_name,
        "model": prompt_version["provider_payload_json"].get("model"),
        "project_id": project_id,
        "shot_db_id": shot["id"],
        "shot_id": shot["shot_id"],
        "duration": prompt_version["provider_payload_json"].get("duration"),
        "resolution": prompt_version["provider_payload_json"].get("resolution"),
        "reference_assets": _reference_asset_version(prompt_version),
        "prompt_version_id": prompt_version_id,
        "estimated_cost": estimate,
        "idempotency_key": idempotency_key,
        "idempotency_key_prefix": idempotency_key[:12],
        "confirmation_token": idempotency_key[:12],
        "confirmation_text": REAL_API_CONFIRM_TEXT,
    }


def _ensure_budget(project: dict, shot: dict, tier: str) -> None:
    takes = [_decode_take(dict(row)) for row in take_repository.list_takes_for_shot(shot["id"])]
    current_tier_count = sum(1 for take in takes if take["tier"] == tier)
    limit = shot.get(f"max_{tier}_takes") or project.get(f"max_{tier}_takes") or (5 if tier == "draft" else 3)
    if current_tier_count >= int(limit):
        raise ValueError(f"{tier} take budget exceeded for shot {shot['shot_id']}")
    total_cost = sum(float(take.get("estimated_cost") or 0) for take in takes)
    shot_budget = shot.get("max_cost_cny") or project.get("max_cost_cny") or 300
    if total_cost >= float(shot_budget):
        raise ValueError(f"Cost budget exceeded for shot {shot['shot_id']}")
    total_duration = sum(int((take.get("generation_settings_json") or {}).get("provider_payload", {}).get("duration", 0)) for take in takes)
    duration_budget = shot.get("max_generation_seconds") or project.get("max_generation_seconds") or 180
    if total_duration >= int(duration_budget):
        raise ValueError(f"Generation seconds budget exceeded for shot {shot['shot_id']}")


def _build_mock_video(project_id: int, shot: dict, take_number: int) -> tuple[str, list[str]]:
    take_dir = OUTPUTS_DIR / str(project_id) / "shots" / shot["shot_id"] / "takes" / str(take_number)
    take_dir.mkdir(parents=True, exist_ok=True)
    video_path = take_dir / "video.mp4"
    color = "0xDCD2B4" if shot["purpose"] == "product_structure_proof" else "0xB4C7DC"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=1280x720:d={shot['duration']}",
            "-pix_fmt",
            "yuv420p",
            str(video_path),
        ],
        check=True,
        capture_output=True,
    )
    qc_paths = _extract_frames(video_path)
    return str(video_path), qc_paths


def _extract_frames(video_path: Path) -> list[str]:
    output_dir = video_path.parent
    first_frame = output_dir / "first_frame.jpg"
    last_frame = output_dir / "last_frame.jpg"
    qc_frames = [output_dir / "qc_25.jpg", output_dir / "qc_50.jpg", output_dir / "qc_75.jpg"]
    subprocess.run(["ffmpeg", "-y", "-i", str(video_path), "-vf", "select=eq(n\\,0)", "-vframes", "1", str(first_frame)], check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-sseof", "-0.1", "-i", str(video_path), "-vframes", "1", str(last_frame)], check=True, capture_output=True)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(video_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    duration = max(float(probe.stdout.strip() or "1"), 0.2)
    timestamps = [duration * 0.25, duration * 0.5, duration * 0.75]
    for timestamp, path in zip(timestamps, qc_frames):
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(video_path), "-vframes", "1", str(path)],
            check=True,
            capture_output=True,
        )
    return [str(first_frame), str(last_frame), *(str(path) for path in qc_frames)]


def _download_provider_video(video_url: str, project_id: int, shot: dict, take_number: int) -> tuple[str, list[str]]:
    take_dir = OUTPUTS_DIR / str(project_id) / "shots" / shot["shot_id"] / "takes" / str(take_number)
    take_dir.mkdir(parents=True, exist_ok=True)
    video_path = take_dir / "video.mp4"
    if not video_path.exists() or video_path.stat().st_size <= 0:
        response = requests.get(video_url, timeout=(10, 180), stream=True)
        response.raise_for_status()
        with video_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
    qc_paths = _extract_frames(video_path)
    return str(video_path), qc_paths


def _safe_url_context(url: str | None) -> dict:
    if not url:
        return {"has_url": False}
    parsed = urlsplit(url)
    return {
        "has_url": True,
        "scheme": parsed.scheme,
        "host": parsed.netloc,
        "path": parsed.path,
        "has_query": bool(parsed.query),
        "query_keys": sorted({key for key, _value in parse_qsl(parsed.query, keep_blank_values=True)}),
    }


def _safe_download_error(error: Exception, video_url: str | None) -> dict:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {}) or {}
    history = getattr(response, "history", []) or []
    return {
        "code": "download_failed",
        "message": sanitize(str(error)),
        "http_status": getattr(response, "status_code", None),
        "content_type": headers.get("content-type") or headers.get("Content-Type"),
        "content_length": headers.get("content-length") or headers.get("Content-Length"),
        "url": _safe_url_context(getattr(response, "url", None) or video_url),
        "redirects": [
            {
                "status": getattr(item, "status_code", None),
                "url": _safe_url_context(getattr(item, "url", None)),
            }
            for item in history
        ],
        "recovery": "download_recovery_required",
    }


def compile_prompt(*, project_id: int, shot_id: int) -> dict:
    project = dict(project_repository.get_project(project_id))
    shots = {shot["id"]: shot for shot in list_shots(project_id)}
    shot = shots[shot_id]
    product_truth = get_latest_product_truth(project_id)
    assets = list_assets(project_id)
    continuity_context = build_continuity_context(project_id, shot)
    role_assets = list(shot["reference_roles_json"])
    identity_mode = shot["mode"] in {"I2V", "R2V", "FLF2V", "edit", "extend"}
    needs_identity_anchor = identity_mode and not any(item.get("primary_role") == "product_identity" for item in role_assets)
    if continuity_context["reanchor_identity"] or needs_identity_anchor:
        for asset in assets:
            if asset["primary_role"] == "product_identity" and asset.get("user_approved"):
                if not any(item.get("primary_role") == "product_identity" for item in role_assets):
                    role_assets.insert(
                        0,
                        {
                            "asset_id": asset["id"],
                            "primary_role": "product_identity",
                            "must_transfer": asset["must_transfer_json"],
                            "must_not_transfer": asset["must_not_transfer_json"],
                        },
                    )
                break
    role_map = {"assets": role_assets, "warnings": [], "reanchor_identity": continuity_context["reanchor_identity"]}
    provider = get_provider()
    provider_capabilities = provider.validate_capabilities(mode=shot["mode"], reference_roles=role_assets)
    resolved_references = resolve_provider_references(assets, role_assets)
    compiler_input = normalize_input(
        CompilerInput(
            project=project,
            shot=shot,
            product_truth=product_truth,
            role_map=role_map,
            continuity_state=continuity_context["ledger"],
            mode=shot["mode"],
            provider_capabilities=provider_capabilities,
            user_constraints=shot["constraints_json"],
        )
    )
    text = select_mode_template(compiler_input)
    text = inject_product_constraints(compiler_input, text)
    text = inject_reference_role_map(compiler_input, text)
    text = inject_continuity_anchors(compiler_input, text)
    raw_draft = build_director_prompt(compiler_input, text)
    anti_slop, removed = anti_slop_pass(raw_draft)
    issues = detect_conflicts(compiler_input, anti_slop)
    anti_slop, beat_issues = enforce_single_visible_beat(compiler_input, anti_slop)
    anti_slop, cam_issues = enforce_single_camera_move(compiler_input, anti_slop)
    issues.extend(beat_issues)
    issues.extend(cam_issues)
    compressed, removed_budget, fits = compress_to_budget(compiler_input, anti_slop)
    removed.extend(removed_budget)
    final_prompt = compressed
    issues = validate_final_prompt(compiler_input, final_prompt, issues)
    payload = provider.build_payload(
        prompt_text=final_prompt,
        mode=shot["mode"],
        ratio=project["aspect_ratio"],
        duration=shot["duration"],
        resolution=project["resolution"],
        generate_audio=SEEDANCE_GENERATE_AUDIO and shot["audio_contract_json"].get("priority") != "mute",
        reference_roles=role_assets,
        resolved_references=resolved_references,
    )
    blocking = [issue for issue in issues if issue.severity == "blocking_error"]
    result = PromptCompileResult(
        mode=shot["mode"],
        raw_draft_prompt=raw_draft,
        anti_slop_prompt=anti_slop,
        compressed_prompt=compressed,
        final_prompt=final_prompt,
        prompt_char_count=len(final_prompt),
        removed_items=removed,
        role_map_json=role_map,
        provider_payload_json=payload,
        compiler_warnings_json=[issue.message for issue in issues if issue.severity != "blocking_error"],
        lint_issues=issues,
        validation_result_json={"fits_budget": fits, "provider_supported": provider_capabilities["supported"], "reanchor_identity": continuity_context["reanchor_identity"]},
        allow_submit=not blocking and provider_capabilities["supported"] and product_truth and product_truth["user_approved"],
    )
    version = shot_repository.get_next_prompt_version(shot_id)
    prompt_version_id = shot_repository.create_prompt_version(
        {
            "shot_id": shot_id,
            "version": version,
            "mode": result.mode,
            "prompt_text": result.final_prompt,
            "prompt_char_count": result.prompt_char_count,
            "prompt_language": project["language"],
            "role_map_json": result.role_map_json,
            "compiler_warnings_json": result.compiler_warnings_json + [f"{issue.severity}:{issue.code}" for issue in result.lint_issues],
            "validation_result_json": result.validation_result_json | {"lint_issues": [issue.model_dump() for issue in result.lint_issues]},
            "raw_draft_prompt": result.raw_draft_prompt,
            "anti_slop_prompt": result.anti_slop_prompt,
            "compressed_prompt": result.compressed_prompt,
            "removed_items_json": result.removed_items,
            "provider_payload_json": result.provider_payload_json,
            "allow_submit": result.allow_submit,
            "locked_by_user": False,
        }
    )
    row = shot_repository.get_prompt_version(prompt_version_id)
    return _decode_prompt_version(dict(row))


def lock_prompt(prompt_version_id: int) -> None:
    row = shot_repository.get_prompt_version(prompt_version_id)
    if not row:
        raise KeyError(f"Prompt version {prompt_version_id} not found")
    shot_repository.update_prompt_version(prompt_version_id, {"locked_by_user": 1})


def _dependency_complete(project_id: int, shot: dict) -> bool:
    schedule = compute_schedule_state(
        list_shots(project_id),
        {item["shot_id"] for item in list_shots(project_id) if item.get("selected_take_id")},
        {item["shot_id"] for item in list_shots(project_id) if item.get("status") == "failed"},
    )
    state = next(item for item in schedule if item["shot_id"] == shot["shot_id"])
    return not state["waiting_on"] and not state["blocked_by_failure"] and not state["cycle"]


def preflight(project_id: int, shot_id: int, prompt_version_id: int, tier: str) -> dict:
    project = dict(project_repository.get_project(project_id))
    shot = next(shot for shot in list_shots(project_id) if shot["id"] == shot_id)
    product_truth = get_latest_product_truth(project_id)
    prompt_version = _decode_prompt_version(dict(shot_repository.get_prompt_version(prompt_version_id)))
    provider = get_provider()
    provider_capabilities = provider.validate_capabilities(mode=shot["mode"], reference_roles=prompt_version["role_map_json"].get("assets", []))
    result = run_preflight(
        project=project,
        shot=shot,
        product_truth=product_truth,
        assets=list_assets(project_id),
        prompt_version=prompt_version,
        provider_capabilities=provider_capabilities,
        tier=tier,
        dependency_complete=_dependency_complete(project_id, shot),
        provider_name=get_video_provider_mode(),
    )
    project_repository.create_preflight_check(
        {
            "project_id": project_id,
            "shot_id": shot_id,
            "prompt_version_id": prompt_version_id,
            "tier": tier,
            "allow_submit": result["allow_submit"],
            "result_json": result,
        }
    )
    return result


def _provider_usage_event_key(submission_id: int) -> str:
    return f"provider_generation:{submission_id}"


def _ensure_provider_usage_event(
    *,
    submission: dict,
    project: dict,
    shot: dict,
    prompt_version: dict,
    provider,
    take_id: int | None,
    status_response: dict,
) -> int:
    return project_repository.create_usage_event(
        {
            "project_id": project["id"],
            "shot_id": shot["id"],
            "take_id": take_id,
            "stage": f"seedance_{submission['generation_tier']}",
            "provider": "ark",
            "model": prompt_version["provider_payload_json"]["model"],
            "duration": prompt_version["provider_payload_json"]["duration"],
            "resolution": prompt_version["provider_payload_json"]["resolution"],
            "estimated_cost": provider.estimate_cost(
                duration=prompt_version["provider_payload_json"]["duration"],
                resolution=prompt_version["provider_payload_json"]["resolution"],
            ),
            "status": "succeeded",
            "raw_usage_json": sanitize(status_response),
            "event_key": _provider_usage_event_key(submission["id"]),
            "source_type": "generation_submission",
            "source_id": submission["id"],
        }
    )


def _finalize_provider_success(
    *,
    submission_id: int,
    project: dict,
    shot: dict,
    prompt_version: dict,
    provider,
    status_response: dict,
) -> dict:
    submission = take_repository.get_generation_submission(submission_id)
    if not submission:
        raise KeyError(f"Generation submission {submission_id} not found")
    extracted = provider.extract_result(status_response)
    task_id = submission.get("provider_task_id") or extracted.get("task_id")
    video_url = extracted.get("video_url")
    existing_take = None
    if submission.get("take_id"):
        existing_take = take_repository.get_take(int(submission["take_id"]))
    if not existing_take:
        existing_take = take_repository.get_take_by_generation_submission_id(submission_id)
    if existing_take:
        take = dict(existing_take)
        take_id = int(take["id"])
        _ensure_provider_usage_event(
            submission=submission,
            project=project,
            shot=shot,
            prompt_version=prompt_version,
            provider=provider,
            take_id=take_id,
            status_response=status_response or submission.get("response_json") or {},
        )
        shot_repository.update_shot(shot["id"], {"status": "generated"})
        take_repository.update_generation_submission(
            submission_id,
            {
                "submission_status": "succeeded",
                "take_id": take_id,
                "submission_completed_at": submission.get("submission_completed_at") or utc_now(),
                "response_json": sanitize(status_response or submission.get("response_json") or {}),
            },
        )
        return {"submission_id": submission_id, "take_id": take_id, "status": "succeeded", "reused": True}
    if not video_url:
        take_repository.update_generation_submission(
            submission_id,
            {
                "submission_status": "failed",
                "error_json": {"code": "missing_video_url", "message": "Provider succeeded but no video_url was returned."},
                "submission_completed_at": utc_now(),
            },
        )
        return {"submission_id": submission_id, "status": "failed", "error": "missing_video_url"}
    _ensure_provider_usage_event(
        submission=submission,
        project=project,
        shot=shot,
        prompt_version=prompt_version,
        provider=provider,
        take_id=None,
        status_response=status_response,
    )
    take_repository.update_generation_submission(submission_id, {"submission_status": "downloading", "response_json": sanitize(status_response)})
    take_number = take_repository.get_next_take_number(shot["id"])
    try:
        local_path, qc_paths = _download_provider_video(video_url, project["id"], shot, take_number)
    except Exception as exc:
        error_json = _safe_download_error(exc, video_url)
        take_repository.update_generation_submission(
            submission_id,
            {
                "submission_status": "download_failed",
                "error_json": error_json,
                "response_json": sanitize(status_response),
            },
        )
        return {"submission_id": submission_id, "status": "download_recovery_required", "error": "download_failed", "http_status": error_json.get("http_status")}
    first_frame_path = qc_paths[0]
    last_frame_path = qc_paths[1]
    take, created = take_repository.get_or_create_take_for_submission(
        {
            "shot_id": shot["id"],
            "take_number": take_number,
            "prompt_version_id": prompt_version["id"],
            "seedance_task_id": task_id,
            "status": "completed",
            "local_path": local_path,
            "remote_url": video_url,
            "first_frame_path": first_frame_path,
            "last_frame_path": last_frame_path,
            "seed": random.randint(1, 999999),
            "generation_settings_json": {"tier": submission["generation_tier"], "provider_payload": sanitize(prompt_version["provider_payload_json"]), "provider": "ark"},
            "estimated_cost": provider.estimate_cost(
                duration=prompt_version["provider_payload_json"]["duration"],
                resolution=prompt_version["provider_payload_json"]["resolution"],
            ),
            "tier": submission["generation_tier"],
            "source_asset_ids_json": [item.get("asset_id") for item in prompt_version["role_map_json"].get("assets", []) if item.get("asset_id")],
            "qc_frame_paths_json": qc_paths,
            "idempotency_key": submission["idempotency_key"],
            "submission_status": "succeeded",
            "provider_task_id": task_id,
            "provider_request_hash": submission["provider_request_hash"],
            "submission_started_at": submission.get("submission_started_at"),
            "submission_completed_at": utc_now(),
            "retry_count": submission.get("retry_count") or 0,
            "last_poll_at": utc_now(),
            "generation_submission_id": submission_id,
        }
    )
    take_id = int(take["id"])
    if created:
        record_continuity_from_take(project_id=project["id"], shot=shot, take_id=take_id, take_row={"first_frame_path": first_frame_path, "last_frame_path": last_frame_path})
    take_repository.update_generation_submission(
        submission_id,
        {"submission_status": "succeeded", "take_id": take_id, "submission_completed_at": utc_now(), "response_json": sanitize(status_response)},
    )
    _ensure_provider_usage_event(
        submission=submission,
        project=project,
        shot=shot,
        prompt_version=prompt_version,
        provider=provider,
        take_id=take_id,
        status_response=status_response,
    )
    shot_repository.update_shot(shot["id"], {"status": "generated"})
    return {"submission_id": submission_id, "take_id": take_id, "status": "succeeded"}


def _run_or_enqueue_provider_submission(submission: dict, *, idempotency_key: str, preflight_result: dict, created: bool) -> dict:
    if submission.get("take_id") and submission.get("submission_status") == "succeeded":
        task_result = process_generation_submission(submission["id"])
        return {"take_id": task_result.get("take_id"), "submission": take_repository.get_generation_submission(submission["id"]), "preflight": preflight_result, "reused": True, "task_result": task_result}
    if requires_manual_reconciliation(submission):
        task_result = _manual_reconciliation_response(submission)
        return {"take_id": submission.get("take_id"), "submission": take_repository.get_generation_submission(submission["id"]), "preflight": preflight_result, "queued": False, "created": created, "task_result": task_result}
    if not can_enqueue_submission(submission):
        reason = "budget_approval_required" if not submission.get("budget_approved_at") and not submission.get("provider_task_id") else "manual_reconciliation_required"
        task_result = _manual_reconciliation_response(submission, reason=reason)
        return {"take_id": submission.get("take_id"), "submission": take_repository.get_generation_submission(submission["id"]), "preflight": preflight_result, "queued": False, "created": created, "task_result": task_result}
    if os.getenv("V3_SYNC_PROVIDER_TASK_FOR_TESTS", "false").lower() == "true":
        task_result = process_generation_submission(submission["id"])
        return {"take_id": task_result.get("take_id"), "submission": take_repository.get_generation_submission(submission["id"]), "preflight": preflight_result, "queued": False, "created": created, "task_result": task_result}
    try:
        from task_queue import enqueue_v3_generation_job

        take_repository.update_generation_submission(submission["id"], {"submission_status": "queued"})
        job = enqueue_v3_generation_job(submission["id"], idempotency_key)
        take_repository.update_generation_submission(submission["id"], {"rq_job_id": job.id})
    except Exception as exc:
        take_repository.update_generation_submission(submission["id"], {"error_json": {"queue_error": str(exc)}})
        raise
    return {"take_id": submission.get("take_id"), "submission": take_repository.get_generation_submission(submission["id"]), "preflight": preflight_result, "queued": True, "created": created}


def submit_generation(
    project_id: int,
    shot_id: int,
    prompt_version_id: int,
    tier: str,
    *,
    parent_take_id: int | None = None,
    changed_variable: str | None = None,
    previous_value: str | None = None,
    new_value: str | None = None,
    change_reason: str = "",
    uncontrolled_revision: bool = False,
    change_count: int = 1,
    paid_confirmed: bool = False,
    paid_confirmation_token: str | None = None,
) -> dict:
    preflight_result = preflight(project_id, shot_id, prompt_version_id, tier)
    if not preflight_result["allow_submit"]:
        raise ValueError("Preflight failed; generation submission blocked.")
    project = dict(project_repository.get_project(project_id))
    shot = next(item for item in list_shots(project_id) if item["id"] == shot_id)
    if change_count > 1 and not uncontrolled_revision:
        raise ValueError("Single-variable retake rule violated.")
    provider = get_provider()
    prompt_version = _decode_prompt_version(dict(shot_repository.get_prompt_version(prompt_version_id)))
    provider_name = get_video_provider_mode()
    idempotency_key = build_idempotency_key(
        project_id=project_id,
        shot_id=shot_id,
        prompt_version_id=prompt_version_id,
        tier=tier,
        provider=provider_name,
        prompt_version=prompt_version,
    )
    if provider_name == "ark":
        if not real_api_enabled():
            raise ValueError("Real Ark generation is disabled. Set V3_REAL_API_ENABLED=true and confirm paid generation.")
        expected_token = idempotency_key[:12]
        if not paid_confirmed or paid_confirmation_token != expected_token:
            raise ValueError("Paid generation confirmation is required for real Ark submission.")
        request_hash = _sha256_text(_canonical_json(sanitize(prompt_version["provider_payload_json"])))
        existing_submission = take_repository.get_generation_submission_by_key(idempotency_key)
        if existing_submission:
            return _run_or_enqueue_provider_submission(existing_submission, idempotency_key=idempotency_key, preflight_result=preflight_result, created=False)
        _ensure_budget(project, shot, tier)
        submission, created = take_repository.reserve_generation_submission(
            {
                "project_id": project_id,
                "shot_id": shot_id,
                "prompt_version_id": prompt_version_id,
                "generation_tier": tier,
                "provider": provider_name,
                "idempotency_key": idempotency_key,
                "provider_request_hash": request_hash,
                "submission_status": "reserved",
                "paid_confirmed": True,
                "confirmation_token": expected_token,
                "request_payload_json": sanitize(prompt_version["provider_payload_json"]),
                "budget_approved_at": utc_now(),
            }
        )
        return _run_or_enqueue_provider_submission(submission, idempotency_key=idempotency_key, preflight_result=preflight_result, created=created)

    _ensure_budget(project, shot, tier)
    take_number = take_repository.get_next_take_number(shot_id)
    local_path, qc_paths = _build_mock_video(project_id, shot, take_number)
    first_frame_path = qc_paths[0]
    last_frame_path = qc_paths[1]
    provider_result = {
        "request": sanitize(prompt_version["provider_payload_json"]),
        "response": {"task_id": f"mock-{shot_id}-{tier}-{take_number}", "status": "completed"},
    }
    take_id = take_repository.create_take(
        {
            "shot_id": shot_id,
            "take_number": take_number,
            "prompt_version_id": prompt_version_id,
            "seedance_task_id": provider_result["response"].get("task_id"),
            "status": "completed",
            "local_path": local_path,
            "first_frame_path": first_frame_path,
            "last_frame_path": last_frame_path,
            "seed": random.randint(1, 999999),
            "generation_settings_json": {"tier": tier, "provider_payload": sanitize(prompt_version["provider_payload_json"])},
            "estimated_cost": provider.estimate_cost(duration=prompt_version["provider_payload_json"]["duration"], resolution=prompt_version["provider_payload_json"]["resolution"]),
            "tier": tier,
            "changed_variable": changed_variable,
            "previous_value": previous_value,
            "new_value": new_value,
            "change_reason": change_reason,
            "source_asset_ids_json": [item.get("asset_id") for item in prompt_version["role_map_json"].get("assets", []) if item.get("asset_id")],
            "qc_frame_paths_json": qc_paths,
            "parent_take_id": parent_take_id,
            "selected_by_user": False,
            "uncontrolled_revision": uncontrolled_revision or change_count > 1,
            "idempotency_key": idempotency_key,
            "submission_status": "succeeded",
            "provider_task_id": provider_result["response"].get("task_id"),
            "provider_request_hash": _sha256_text(_canonical_json(sanitize(prompt_version["provider_payload_json"]))),
            "submission_started_at": utc_now(),
            "submission_completed_at": utc_now(),
        }
    )
    record_continuity_from_take(project_id=project_id, shot=shot, take_id=take_id, take_row={"first_frame_path": first_frame_path, "last_frame_path": last_frame_path})
    shot_repository.update_shot(shot_id, {"status": "generated"})
    project_repository.update_project(project_id, {"current_stage": "production_generation" if tier == "production" else "draft_generation"})
    project_repository.create_usage_event(
        {
            "project_id": project_id,
            "shot_id": shot_id,
            "take_id": take_id,
            "stage": f"seedance_{tier}",
            "provider": "mock",
            "model": prompt_version["provider_payload_json"]["model"],
            "duration": prompt_version["provider_payload_json"]["duration"],
            "resolution": prompt_version["provider_payload_json"]["resolution"],
            "estimated_cost": provider.estimate_cost(duration=prompt_version["provider_payload_json"]["duration"], resolution=prompt_version["provider_payload_json"]["resolution"]),
            "status": "succeeded",
            "raw_usage_json": provider_result,
        }
    )
    return {"take_id": take_id, "provider_result": provider_result, "preflight": preflight_result}


def process_generation_submission(submission_id: int) -> dict:
    submission = take_repository.get_generation_submission(submission_id)
    if not submission:
        raise KeyError(f"Generation submission {submission_id} not found")
    project = dict(project_repository.get_project(submission["project_id"]))
    shot = next(item for item in list_shots(submission["project_id"]) if item["id"] == submission["shot_id"])
    prompt_version = _decode_prompt_version(dict(shot_repository.get_prompt_version(submission["prompt_version_id"])))
    provider = get_provider()
    if submission.get("take_id") and submission["submission_status"] == "succeeded":
        return _finalize_provider_success(
            submission_id=submission_id,
            project=project,
            shot=shot,
            prompt_version=prompt_version,
            provider=provider,
            status_response=submission.get("response_json") or {},
        )
    if requires_manual_reconciliation(submission):
        return _manual_reconciliation_response(submission)
    should_call_submit = False
    if not submission.get("provider_task_id"):
        if not can_call_provider_submit(submission):
            reason = "budget_approval_required" if not submission.get("budget_approved_at") else "manual_reconciliation_required"
            return _manual_reconciliation_response(submission, reason=reason)
        if not take_repository.claim_generation_submission_for_submit(submission_id):
            refreshed = take_repository.get_generation_submission(submission_id)
            if refreshed and can_poll_existing_task(refreshed):
                submission = refreshed
            else:
                return _manual_reconciliation_response(refreshed or submission)
        else:
            submission = take_repository.get_generation_submission(submission_id) or submission
            should_call_submit = True
    if should_call_submit:
        try:
            provider_result = provider.submit_task(prompt_version["provider_payload_json"])
        except (requests.Timeout, requests.ConnectionError) as exc:
            take_repository.update_generation_submission(
                submission_id,
                {
                    "submission_status": "unknown_submission_state",
                    "error_json": provider.normalize_error(exc) | {"possible_charge": True},
                    "retry_count": int(submission.get("retry_count") or 0) + 1,
                },
            )
            return {"submission_id": submission_id, "status": "unknown_submission_state", "possible_charge": True}
        except Exception as exc:
            take_repository.update_generation_submission(
                submission_id,
                {
                    "submission_status": "failed",
                    "error_json": provider.normalize_error(exc),
                    "submission_completed_at": utc_now(),
                    "retry_count": int(submission.get("retry_count") or 0) + 1,
                },
            )
            return {"submission_id": submission_id, "status": "failed"}
        extracted = provider.extract_result(provider_result.get("response", {}))
        task_id = extracted.get("task_id") or provider_result.get("response", {}).get("task_id")
        take_repository.update_generation_submission(
            submission_id,
            {"submission_status": "submitted", "provider_task_id": task_id, "response_json": sanitize(provider_result.get("response", {}))},
        )
        submission = take_repository.get_generation_submission(submission_id)
    if requires_manual_reconciliation(submission):
        return _manual_reconciliation_response(submission)
    if not can_poll_existing_task(submission):
        return _manual_reconciliation_response(submission)
    task_id = submission.get("provider_task_id")
    poll_deadline = time.time() + int(os.getenv("V3_PROVIDER_POLL_TIMEOUT_SECONDS", "900"))
    poll_interval = float(os.getenv("V3_PROVIDER_POLL_INTERVAL_SECONDS", "5"))
    final_response = {}
    while time.time() < poll_deadline:
        take_repository.update_generation_submission(submission_id, {"submission_status": "polling", "last_poll_at": utc_now()})
        try:
            status_response = provider.get_task_status(task_id)
        except Exception as exc:
            take_repository.update_generation_submission(submission_id, {"error_json": provider.normalize_error(exc)})
            time.sleep(min(poll_interval, 1.0))
            continue
        final_response = status_response
        extracted = provider.extract_result(status_response)
        status = (extracted.get("status") or "").lower()
        if status in {"succeeded", "success", "completed"}:
            return _finalize_provider_success(
                submission_id=submission_id,
                project=project,
                shot=shot,
                prompt_version=prompt_version,
                provider=provider,
                status_response=status_response,
            )
        if status in {"failed", "cancelled", "canceled"}:
            terminal = "cancelled" if status in {"cancelled", "canceled"} else "failed"
            take_repository.update_generation_submission(submission_id, {"submission_status": terminal, "response_json": sanitize(status_response), "submission_completed_at": utc_now()})
            return {"submission_id": submission_id, "status": terminal}
        time.sleep(min(poll_interval, 1.0))
    take_repository.update_generation_submission(submission_id, {"submission_status": "poll_timeout", "error_json": {"code": "poll_timeout", "message": "Provider polling timed out."}, "submission_completed_at": utc_now(), "response_json": sanitize(final_response)})
    return {"submission_id": submission_id, "status": "poll_timeout", "error": "poll_timeout"}


def recover_generation_submission(submission_id: int) -> dict:
    submission = take_repository.get_generation_submission(submission_id)
    if not submission:
        raise KeyError(f"Generation submission {submission_id} not found")
    if not submission.get("provider_task_id"):
        return _manual_reconciliation_response(submission, reason="provider_task_id_required")
    project = dict(project_repository.get_project(submission["project_id"]))
    shot = next(item for item in list_shots(submission["project_id"]) if item["id"] == submission["shot_id"])
    prompt_version = _decode_prompt_version(dict(shot_repository.get_prompt_version(submission["prompt_version_id"])))
    provider = get_provider()
    status_response = provider.get_task_status(submission["provider_task_id"])
    extracted = provider.extract_result(status_response)
    status = (extracted.get("status") or "").lower()
    if status in {"succeeded", "success", "completed"}:
        return _finalize_provider_success(
            submission_id=submission_id,
            project=project,
            shot=shot,
            prompt_version=prompt_version,
            provider=provider,
            status_response=status_response,
        )
    if status in {"failed", "cancelled", "canceled"}:
        terminal = "cancelled" if status in {"cancelled", "canceled"} else "failed"
        take_repository.update_generation_submission(
            submission_id,
            {"submission_status": terminal, "response_json": sanitize(status_response), "submission_completed_at": utc_now()},
        )
        return {"submission_id": submission_id, "status": terminal}
    take_repository.update_generation_submission(
        submission_id,
        {"submission_status": "polling", "last_poll_at": utc_now(), "response_json": sanitize(status_response)},
    )
    return {"submission_id": submission_id, "status": "polling", "provider_status": status or "unknown"}
