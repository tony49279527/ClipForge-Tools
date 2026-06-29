from __future__ import annotations

from clipforge_v3.schemas.assets import ReferenceRoleMap


ROLE_DEFAULTS = {
    "product_identity": {
        "must_transfer": ["overall geometry", "core silhouette", "visible structural anchors"],
        "must_not_transfer": ["background", "camera angle", "lighting"],
    },
    "product_geometry": {
        "must_transfer": ["side profile", "thickness", "mounting hole geometry"],
        "must_not_transfer": ["background", "packaging text"],
    },
    "material_detail": {
        "must_transfer": ["surface material", "fiber texture", "color family"],
        "must_not_transfer": ["framing", "environment"],
    },
    "installation": {
        "must_transfer": ["assembly relationship", "fastener order", "mounting direction"],
        "must_not_transfer": ["background", "styling"],
    },
    "fact_evidence": {
        "must_transfer": ["evidence only"],
        "must_not_transfer": ["composition", "style"],
    },
}


def build_reference_role_map(*, shot_purpose: str, assets: list[dict]) -> dict:
    warnings: list[str] = []
    assignments = []
    used_roles: dict[int, list[str]] = {}
    for asset in assets:
        role = asset["primary_role"]
        used_roles.setdefault(asset["id"], []).append(role)
        defaults = ROLE_DEFAULTS.get(role, {"must_transfer": [], "must_not_transfer": []})
        if asset.get("secondary_role"):
            warnings.append(f"Asset {asset['id']} has a secondary role and may be overloaded.")
        assignments.append(
            {
                "asset_id": asset["id"],
                "primary_role": role,
                "must_transfer": asset.get("must_transfer_json") or defaults["must_transfer"],
                "must_not_transfer": asset.get("must_not_transfer_json") or defaults["must_not_transfer"],
            }
        )
    for asset in assets:
        roles = [assignment["primary_role"] for assignment in assignments if assignment["asset_id"] == asset["id"]]
        if len(set(roles)) > 1:
            warnings.append(f"Asset {asset['id']} is overloaded across multiple roles and should be downgraded.")
    if "installation" in shot_purpose and not any(asset["primary_role"] == "installation" for asset in assets):
        warnings.append("Installation shot is missing an installation role asset.")
    if not any(asset["primary_role"] == "product_identity" for asset in assets):
        warnings.append("No product identity anchor is available for this shot.")
    return ReferenceRoleMap(assets=assignments, warnings=warnings).model_dump()
