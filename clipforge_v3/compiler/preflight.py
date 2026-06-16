from __future__ import annotations

from clipforge_v3.providers.config import SEEDANCE_PROMPT_MAX_CHARS
from clipforge_v3.providers.seedance_ark import FAIL_CLOSED_ROLES, FAIL_OPEN_ROLES
from clipforge_v3.schemas.generation import PreflightCheckItem, PreflightResult


def run_preflight(*, project: dict, shot: dict, product_truth: dict | None, assets: list[dict], prompt_version: dict, provider_capabilities: dict, tier: str, dependency_complete: bool, provider_name: str = "mock") -> dict:
    items = []
    items.append(PreflightCheckItem(name="product_truth_confirmed", passed=bool(product_truth and product_truth["user_approved"]), message="Product Truth must be confirmed."))
    items.append(PreflightCheckItem(name="shot_contract_confirmed", passed=bool(shot.get("user_approved")), message="Shot Contract must be confirmed."))
    items.append(PreflightCheckItem(name="prompt_allow_submit", passed=bool(prompt_version.get("allow_submit")), message="Prompt must pass compiler and linter."))
    roles = {asset["primary_role"]: asset for asset in assets}
    required_roles = prompt_version.get("provider_payload_json", {}).get("content", [])
    for role_name in FAIL_CLOSED_ROLES:
        if any(entry.get("role") == role_name or entry.get("reference_role") == role_name for entry in required_roles):
            asset = roles.get(role_name)
            items.append(PreflightCheckItem(name=f"required_asset_{role_name}", passed=bool(asset and asset.get("user_approved")), message=f"{role_name} asset must exist and be confirmed."))
    if provider_name == "ark":
        image_source_roles = {
            entry.get("reference_role")
            for entry in required_roles
            if entry.get("type") == "image_url" and entry.get("image_url", {}).get("url")
        }
        audit_by_role = {entry.get("role"): entry for entry in prompt_version.get("provider_payload_json", {}).get("reference_audit", [])}
        for role_name in FAIL_CLOSED_ROLES:
            if any(entry.get("role") == role_name or entry.get("reference_role") == role_name for entry in required_roles):
                audit = audit_by_role.get(role_name, {})
                has_source = role_name in image_source_roles or bool(audit.get("has_source"))
                items.append(
                    PreflightCheckItem(
                        name=f"provider_asset_source_{role_name}",
                        passed=has_source,
                        message="MISSING_PROVIDER_ASSET_SOURCE: 产品身份素材存在，但没有 Ark 可以访问的 HTTPS 图片地址。",
                    )
                )
    items.append(PreflightCheckItem(name="provider_capabilities", passed=bool(provider_capabilities.get("supported")), message="Provider must support current mode and reference roles."))
    items.append(PreflightCheckItem(name="prompt_char_budget", passed=prompt_version.get("prompt_char_count", 0) <= SEEDANCE_PROMPT_MAX_CHARS, message="Prompt must be within configured char budget."))
    items.append(PreflightCheckItem(name="duration_valid", passed=4 <= int(shot["duration"]) <= 8, message="Shot duration must be within allowed range."))
    items.append(PreflightCheckItem(name="resolution_valid", passed=bool(project.get("resolution")), message="Resolution must be present."))
    items.append(PreflightCheckItem(name="continuity_dependency", passed=dependency_complete, message="Dependent shot must be completed for sequential generation."))
    allow = all(item.passed or item.severity != "blocking_error" for item in items)
    degraded = [role for role in FAIL_OPEN_ROLES if role not in roles]
    return PreflightResult(
        allow_submit=allow,
        tier=tier,
        items=items,
        degraded_roles=sorted(degraded),
        fail_closed_roles=sorted(FAIL_CLOSED_ROLES),
        fail_open_roles=sorted(FAIL_OPEN_ROLES),
    ).model_dump()
