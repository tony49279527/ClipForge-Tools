from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from clipforge_v3.providers.seedance_ark import FAIL_CLOSED_ROLES


def _is_private_or_loopback_host(hostname: str) -> bool:
    host = (hostname or "").strip().lower()
    if not host:
        return True
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast


def validate_public_https_url(url: str | None) -> tuple[bool, str]:
    if not url:
        return False, "empty_url"
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False, "not_https"
    if not parsed.netloc or not parsed.hostname:
        return False, "invalid_url"
    if _is_private_or_loopback_host(parsed.hostname):
        return False, "blocked_host"
    return True, ""


def safe_url_preview(url: str | None) -> dict:
    if not url:
        return {"has_source": False}
    parsed = urlparse(url)
    path = parsed.path or ""
    parts = [part for part in path.split("/") if part]
    path_prefix = "/" + "/".join(parts[:2]) if parts else ""
    return {
        "scheme": parsed.scheme,
        "host": parsed.hostname or "",
        "path_prefix": path_prefix,
        "has_source": bool(parsed.scheme and parsed.hostname),
    }


def resolve_provider_reference(asset: dict | None, role: dict, *, label: str) -> dict:
    primary_role = role.get("primary_role") or (asset or {}).get("primary_role")
    fail_policy = "closed" if primary_role in FAIL_CLOSED_ROLES else "open"
    source_url = (asset or {}).get("remote_url") or (asset or {}).get("access_url")
    valid, reason = validate_public_https_url(source_url)
    resolved = {
        "asset_id": role.get("asset_id") or (asset or {}).get("id"),
        "label": label,
        "primary_role": primary_role,
        "source_type": "image_url" if valid else None,
        "url": source_url if valid else None,
        "must_transfer": role.get("must_transfer") or (asset or {}).get("must_transfer_json", []),
        "must_not_transfer": role.get("must_not_transfer") or (asset or {}).get("must_not_transfer_json", []),
        "fail_policy": fail_policy,
        "available": valid,
        "unavailable_reason": "" if valid else reason,
        "preview": safe_url_preview(source_url if valid else None),
    }
    return resolved


def resolve_provider_references(assets: list[dict], role_map_assets: list[dict]) -> list[dict]:
    assets_by_id = {asset.get("id"): asset for asset in assets}
    assets_by_role = {asset.get("primary_role"): asset for asset in assets}
    resolved = []
    image_index = 1
    for role in role_map_assets:
        asset = assets_by_id.get(role.get("asset_id")) or assets_by_role.get(role.get("primary_role"))
        resolved.append(resolve_provider_reference(asset, role, label=f"Image{image_index}"))
        image_index += 1
    return resolved


def provider_asset_source_issues(resolved_references: list[dict]) -> list[dict]:
    issues = []
    for ref in resolved_references:
        if ref.get("fail_policy") == "closed" and not ref.get("available"):
            issues.append(
                {
                    "code": "MISSING_PROVIDER_ASSET_SOURCE",
                    "role": ref.get("primary_role"),
                    "asset_id": ref.get("asset_id"),
                    "label": ref.get("label"),
                    "reason": ref.get("unavailable_reason"),
                    "message": "产品身份素材存在，但没有 Ark 可以访问的 HTTPS 图片地址。",
                }
            )
    return issues


_validate_https_url = validate_public_https_url
_safe_url_preview = safe_url_preview
