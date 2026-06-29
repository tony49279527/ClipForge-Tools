from __future__ import annotations

from clipforge_v3.compiler.preflight import run_preflight
from clipforge_v3.providers.seedance_ark import FAIL_CLOSED_ROLES, FAIL_OPEN_ROLES, ArkSeedanceProvider


def test_fail_closed_identity_requires_asset():
    result = run_preflight(
        project={"resolution": "1080p"},
        shot={"duration": 5, "user_approved": True},
        product_truth={"user_approved": True},
        assets=[],
        prompt_version={
            "allow_submit": True,
            "prompt_char_count": 100,
            "provider_payload_json": {"content": [{"role": "product_identity"}]},
        },
        provider_capabilities={"supported": True},
        tier="draft",
        dependency_complete=True,
    )
    assert result["allow_submit"] is False


def test_fail_open_style_can_degrade():
    result = run_preflight(
        project={"resolution": "1080p"},
        shot={"duration": 5, "user_approved": True},
        product_truth={"user_approved": True},
        assets=[{"primary_role": "product_identity", "user_approved": True}],
        prompt_version={
            "allow_submit": True,
            "prompt_char_count": 100,
            "provider_payload_json": {"content": [{"role": "product_identity"}]},
        },
        provider_capabilities={"supported": True},
        tier="draft",
        dependency_complete=True,
    )
    assert "style" in result["degraded_roles"]
    assert result["allow_submit"] is True


def test_provider_validate_capabilities():
    provider = ArkSeedanceProvider()
    result = provider.validate_capabilities(mode="FLF2V", reference_roles=[{"primary_role": "product_identity"}])
    assert result["supported"] is False
    assert "first_frame" in result["missing"]


def test_provider_normalize_error():
    provider = ArkSeedanceProvider()
    payload = provider.normalize_error("Unsupported reference role for provider payload: unknown")
    assert payload["code"] == "unsupported_reference_role"


def test_fail_open_closed_constants_present():
    assert "product_identity" in FAIL_CLOSED_ROLES
    assert "style" in FAIL_OPEN_ROLES
