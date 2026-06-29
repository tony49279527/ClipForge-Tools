from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, UnidentifiedImageError

from clipforge_v3.repositories import asset_repository
from clipforge_v3.schemas.assets import AssetAuditReport, AssetAuditWarning, V3AssetRecord
from clipforge_v3.services.storage_service import ALLOWED_IMAGE_MIME, UPLOADS_DIR, get_storage


KEY_ANGLE_ROLES = {"product_identity", "product_geometry", "installation", "material_detail"}


def audit_asset(*, file_path: Path, primary_role: str, project_assets: list[dict], mime_type: str = "image/png") -> AssetAuditReport:
    warnings: list[AssetAuditWarning] = []
    if mime_type in ALLOWED_IMAGE_MIME:
        try:
            with Image.open(file_path) as image:
                width, height = image.size
                has_alpha = "A" in image.getbands()
                format_name = (image.format or file_path.suffix.lstrip(".") or "unknown").lower()
        except UnidentifiedImageError as exc:
            raise ValueError("Image upload failed validation. Re-upload a valid JPG, PNG, or WEBP file.") from exc
    else:
        width = 0
        height = 0
        has_alpha = False
        format_name = mime_type.split("/", 1)[-1]
    size_bytes = file_path.stat().st_size
    is_clear = mime_type not in ALLOWED_IMAGE_MIME or (width >= 1024 and height >= 1024)
    has_perspective_distortion = abs((width / max(height, 1)) - 1.0) > 1.7
    key_structure_visible = is_clear and primary_role in KEY_ANGLE_ROLES and mime_type in ALLOWED_IMAGE_MIME
    if mime_type in ALLOWED_IMAGE_MIME and not is_clear:
        warnings.append(AssetAuditWarning(code="low_resolution", severity="high", message="Resolution is likely too low for identity-critical use."))
    if mime_type not in ALLOWED_IMAGE_MIME and primary_role in KEY_ANGLE_ROLES:
        warnings.append(AssetAuditWarning(code="non_image_identity_role", severity="warning", message="Identity-critical roles should normally use a real product image."))
    if size_bytes < 80_000:
        warnings.append(AssetAuditWarning(code="small_file", severity="warning", message="Small file size may indicate compression artifacts."))
    if has_perspective_distortion and primary_role in {"product_identity", "product_geometry"}:
        warnings.append(AssetAuditWarning(code="perspective_distortion", severity="warning", message="Perspective distortion may weaken geometry transfer."))
    if "watermark" in file_path.name.lower() or "text" in file_path.name.lower():
        warnings.append(AssetAuditWarning(code="possible_text_overlay", severity="warning", message="Filename suggests possible text or watermark contamination."))
    if primary_role == "installation" and width < 1200:
        warnings.append(AssetAuditWarning(code="installation_detail_risk", severity="warning", message="Installation role may need a closer or higher-resolution relationship reference."))
    existing_roles = {asset["primary_role"] for asset in project_assets}
    missing_angles = sorted(list({"product_identity", "product_geometry", "installation"} - existing_roles - {primary_role}))
    conflict_detected = any(
        asset["primary_role"] == primary_role and asset.get("metadata_json", {}).get("width") and abs(asset["metadata_json"]["width"] - width) > 1600
        for asset in project_assets
    )
    if conflict_detected:
        warnings.append(AssetAuditWarning(code="role_conflict", severity="warning", message="Another asset in the same role has very different framing or scale."))
    return AssetAuditReport(
        width=width,
        height=height,
        format=format_name,
        file_size_bytes=size_bytes,
        is_clear=is_clear,
        has_transparent_background=has_alpha,
        has_perspective_distortion=has_perspective_distortion,
        key_structure_visible=key_structure_visible,
        conflict_detected=conflict_detected,
        warnings=warnings,
        missing_project_angles=missing_angles,
    )


def create_asset(
    *,
    project_id: int,
    file_path: Path,
    original_filename: str,
    primary_role: str,
    secondary_role: str | None,
    must_transfer: list[str],
    must_not_transfer: list[str],
    applies_to_shots: list[str],
    is_identity_anchor: bool,
    user_approved: bool,
    mime_type: str = "image/png",
    storage_backend: str = "local",
    access_url: str | None = None,
    object_key: str | None = None,
    content_type: str | None = None,
    size_bytes: int | None = None,
) -> dict:
    existing_assets = list_assets(project_id)
    report = audit_asset(file_path=file_path, primary_role=primary_role, project_assets=existing_assets, mime_type=mime_type)
    asset_type = "image"
    if mime_type.startswith("video/"):
        asset_type = "video"
    elif mime_type.startswith("audio/"):
        asset_type = "audio"
    elif mime_type.startswith("application/") or mime_type.startswith("text/"):
        asset_type = "document"
    asset = V3AssetRecord(
        project_id=project_id,
        asset_type=asset_type,
        original_filename=original_filename,
        local_path=str(file_path),
        remote_url=None,
        mime_type=mime_type,
        primary_role=primary_role,
        secondary_role=secondary_role or None,
        must_transfer_json=must_transfer,
        must_not_transfer_json=must_not_transfer,
        applies_to_shots_json=applies_to_shots,
        is_identity_anchor=is_identity_anchor,
        user_approved=user_approved,
        metadata_json={
            "width": report.width,
            "height": report.height,
            "format": report.format,
            "file_size_bytes": report.file_size_bytes,
            "storage_backend": storage_backend,
            "access_url": access_url,
            "object_key": object_key,
        },
        audit_report=report,
    )
    asset_id = asset_repository.create_asset(
        {
            **asset.model_dump(exclude={"audit_report"}),
            "audit_report_json": asset.audit_report.model_dump(),
            "storage_backend": storage_backend,
            "access_url": access_url,
            "object_key": object_key,
            "content_type": content_type or mime_type,
            "size_bytes": size_bytes or report.file_size_bytes,
        }
    )
    created = asset_repository.get_asset(asset_id)
    return _decode_asset_row(dict(created))


def list_assets(project_id: int) -> list[dict]:
    return [_decode_asset_row(dict(row)) for row in asset_repository.list_assets(project_id) if not row["deleted_at"]]


def delete_asset(asset_id: int) -> None:
    asset_repository.soft_delete_asset(asset_id)


def replace_asset(
    *,
    old_asset_id: int,
    project_id: int,
    file_path: Path,
    original_filename: str,
    primary_role: str,
    secondary_role: str | None,
    must_transfer: list[str],
    must_not_transfer: list[str],
    applies_to_shots: list[str],
    is_identity_anchor: bool,
    user_approved: bool,
    mime_type: str,
    storage_backend: str,
    access_url: str | None,
    object_key: str | None = None,
    content_type: str | None = None,
    size_bytes: int | None = None,
) -> dict:
    created = create_asset(
        project_id=project_id,
        file_path=file_path,
        original_filename=original_filename,
        primary_role=primary_role,
        secondary_role=secondary_role,
        must_transfer=must_transfer,
        must_not_transfer=must_not_transfer,
        applies_to_shots=applies_to_shots,
        is_identity_anchor=is_identity_anchor,
        user_approved=user_approved,
        mime_type=mime_type,
        storage_backend=storage_backend,
        access_url=access_url,
        object_key=object_key,
        content_type=content_type,
        size_bytes=size_bytes,
    )
    asset_repository.replace_asset(old_asset_id, created["id"])
    return created


def create_demo_placeholder_asset(
    *,
    project_id: int,
    primary_role: str = "product_identity",
    secondary_role: str | None = None,
    must_transfer: list[str] | None = None,
    must_not_transfer: list[str] | None = None,
    applies_to_shots: list[str] | None = None,
    is_identity_anchor: bool = True,
    user_approved: bool = True,
) -> dict:
    existing_assets = list_assets(project_id)
    for asset in existing_assets:
        if (
            asset["primary_role"] == primary_role
            and asset.get("metadata_json", {}).get("demo_placeholder")
            and not asset.get("deleted_at")
        ):
            return asset

    must_transfer = must_transfer or [
        "overall wheel geometry",
        "1/2-inch center hole",
        "concentric stitched rings",
        "natural off-white cotton",
    ]
    must_not_transfer = must_not_transfer or ["background", "camera angle", "text overlay"]
    applies_to_shots = applies_to_shots or ["S01", "S02", "S03"]

    demo_dir = (UPLOADS_DIR / "v3" / str(project_id) / "demo").resolve()
    demo_dir.mkdir(parents=True, exist_ok=True)
    image_path = demo_dir / f"{primary_role}_buffing_wheel_demo.png"

    image = Image.new("RGB", (1600, 1600), color=(247, 241, 228))
    draw = ImageDraw.Draw(image)
    center = 800
    palette = {
        "cotton": (230, 221, 198),
        "stitch": (153, 118, 82),
        "hole": (92, 74, 56),
        "accent": (196, 138, 62),
    }
    draw.ellipse((160, 160, 1440, 1440), fill=palette["cotton"], outline=palette["accent"], width=10)
    for radius in (1180, 990, 800, 610):
        left = center - radius // 2
        top = center - radius // 2
        right = center + radius // 2
        bottom = center + radius // 2
        draw.ellipse((left, top, right, bottom), outline=palette["stitch"], width=8)
    draw.ellipse((700, 700, 900, 900), fill=palette["hole"], outline=palette["stitch"], width=10)
    draw.text((470, 1450), "ClipForge demo placeholder", fill=palette["accent"])
    image.save(image_path, format="PNG")

    stored = get_storage().save_file(
        project_id=project_id,
        source_path=image_path,
        content_type="image/png",
    )
    created = create_asset(
        project_id=project_id,
        file_path=Path(stored["local_path"]),
        original_filename="buffing-wheel-demo.png",
        primary_role=primary_role,
        secondary_role=secondary_role,
        must_transfer=must_transfer,
        must_not_transfer=must_not_transfer,
        applies_to_shots=applies_to_shots,
        is_identity_anchor=is_identity_anchor,
        user_approved=user_approved,
        mime_type="image/png",
        storage_backend=stored["backend"],
        access_url=stored["access_url"],
        object_key=stored.get("object_key"),
        content_type=stored.get("content_type"),
        size_bytes=stored.get("size_bytes"),
    )
    created["metadata_json"]["demo_placeholder"] = True
    asset_repository.update_asset(created["id"], {"metadata_json": created["metadata_json"]})
    created["audit_report_json"]["demo_placeholder"] = True
    return created


def _decode_asset_row(payload: dict) -> dict:
    payload["must_transfer_json"] = json.loads(payload["must_transfer_json"] or "[]")
    payload["must_not_transfer_json"] = json.loads(payload["must_not_transfer_json"] or "[]")
    payload["applies_to_shots_json"] = json.loads(payload["applies_to_shots_json"] or "[]")
    payload["metadata_json"] = json.loads(payload["metadata_json"] or "{}")
    payload["audit_report_json"] = json.loads(payload.get("audit_report_json") or "{}")
    payload["is_identity_anchor"] = bool(payload.get("is_identity_anchor"))
    payload["user_approved"] = bool(payload.get("user_approved"))
    return payload
