from __future__ import annotations

import pytest
from pydantic import ValidationError

from clipforge_v3.director.fidelity_allocator import allocate_fidelity
from clipforge_v3.director.mode_router import choose_generation_mode
from clipforge_v3.director.reference_mapper import build_reference_role_map
from clipforge_v3.providers.openai_text import validate_product_truth_json
from clipforge_v3.schemas.assets import AssetAuditReport, V3AssetRecord
from clipforge_v3.schemas.continuity import V3ContinuityStateRecord
from clipforge_v3.schemas.generation import V3PromptVersionRecord, V3TakeRecord
from clipforge_v3.schemas.product_truth import ProductTruthPayload, V3ProductTruthRecord
from clipforge_v3.schemas.project import V3ProjectCreate
from clipforge_v3.schemas.review import V3ReviewRecord
from clipforge_v3.schemas.shot_contract import ShotContractPayload, V3ShotRecord


def test_project_schema_rejects_invalid_duration():
    with pytest.raises(ValidationError):
        V3ProjectCreate(
            project_name="ab",
            product_name="Tool",
            product_category="hardware",
            target_market="US",
            target_audience="buyers",
            target_platform="amazon",
            aspect_ratio="16:9",
            total_duration=4,
            default_clip_duration=6,
            resolution="1080p",
            language="en",
            source_description="too short",
        )


def test_product_truth_json_schema_validates():
    model = validate_product_truth_json(
        '{"product_type":"buffing wheel","immutable_geometry":{"shape":"circular","diameter":"6-inch","thickness":"1-inch","center_hole":"1/2-inch","component_count":"approx. 70 ply","other":[]},"materials":{"correct":["natural off-white cotton"],"forbidden":["wool"]},"colors":["off-white"],"components":["wheel","washer","nut"],"installation":{"required_steps":["spindle through center hole"],"required_relationships":["washer and nut secure wheel"],"forbidden_relationships":["floating mount"]},"working_surface":{"correct":["outer cotton circumference"],"incorrect":["center hole"]},"allowed_behaviors":["metal polishing"],"forbidden_transformations":["do not become grinding stone"],"safety_constraints":["secure fasteners"],"uncertain_facts":["ply count visually approximate"],"source_evidence":[{"source_type":"description","detail":"spec sheet"}]}'
    )
    assert isinstance(model, ProductTruthPayload)
    assert "ply count visually approximate" in model.uncertain_facts


def test_product_truth_schema_rejects_invalid_version():
    with pytest.raises(ValidationError):
        V3ProductTruthRecord(project_id=1, source_description="Valid enough source description", product_truth_json=ProductTruthPayload(product_type="x"), version=0)


def test_asset_schema_rejects_invalid_asset_type():
    with pytest.raises(ValidationError):
        V3AssetRecord(
            project_id=1,
            asset_type="gif",
            original_filename="ref.png",
            primary_role="product_identity",
            audit_report=AssetAuditReport(width=0, height=0, format="png", file_size_bytes=0, is_clear=False, has_transparent_background=False, has_perspective_distortion=False, key_structure_visible=False, conflict_detected=False),
        )


def test_asset_role_conflict_warning():
    role_map = build_reference_role_map(
        shot_purpose="installation_relationship_proof",
        assets=[{"id": 1, "primary_role": "product_identity", "secondary_role": "installation"}],
    )
    assert any("overloaded" in warning for warning in role_map["warnings"])


def test_mode_router_prefers_i2v_or_r2v():
    decision = choose_generation_mode(
        shot_purpose="product_structure_proof",
        strict_identity=True,
        assets=[{"id": 1, "primary_role": "product_identity", "asset_type": "image"}],
        needs_continuity=False,
    )
    assert decision["selected_mode"] in {"I2V", "R2V"}
    assert decision["selected_mode"] != "T2V"


def test_mode_router_missing_identity_warns():
    decision = choose_generation_mode(
        shot_purpose="product_structure_proof",
        strict_identity=True,
        assets=[],
        needs_continuity=False,
    )
    assert "product_identity" in decision["missing_assets"]
    assert decision["risk_level"] == "high"


def test_fidelity_budget_flags_overloaded_shot():
    allocation = allocate_fidelity(
        shot_purpose="installation_relationship_proof",
        strict_identity=True,
        action_complexity="high",
        crowded_scene=True,
        readable_text_required=True,
    )
    assert allocation["split_recommended"] is True


def test_shot_schema_rejects_invalid_duration():
    with pytest.raises(ValidationError):
        V3ShotRecord(
            project_id=1,
            shot_id="S01",
            sequence_index=1,
            purpose="Setup shot",
            mode="I2V",
            duration=3,
            primary_spend="product_identity",
            subject_action="Drive fastener into wood",
            single_visible_beat="beat",
            generation_strategy="parallel",
        )


def test_shot_contract_payload_depends_on_previous():
    payload = ShotContractPayload(
        shot_id="S02",
        purpose="working_surface_proof",
        commercial_beat="show action",
        duration=5,
        mode="I2V",
        primary_spend="product_identity",
        subject_action="Outer circumference touches metal surface",
        single_visible_beat="Correct contact surface is visible",
        depends_on_shot_id="S01",
    )
    assert payload.depends_on_shot_id == "S01"


def test_prompt_version_schema_rejects_short_prompt():
    with pytest.raises(ValidationError):
        V3PromptVersionRecord(
            shot_id=1,
            version=1,
            mode="seedance",
            prompt_text="too short",
            prompt_char_count=9,
            prompt_language="en",
        )


def test_take_schema_rejects_negative_token_usage():
    with pytest.raises(ValidationError):
        V3TakeRecord(shot_id=1, take_number=1, prompt_version_id=1, token_usage=-1)


def test_review_schema_rejects_invalid_verdict():
    with pytest.raises(ValidationError):
        V3ReviewRecord(
            take_id=1,
            verdict="maybe",
            product_identity_score=8,
            mechanical_accuracy_score=8,
            material_accuracy_score=8,
            motion_realism_score=8,
            camera_execution_score=8,
            continuity_score=8,
            commercial_usability_score=8,
            next_action="retry",
        )


def test_continuity_schema_rejects_invalid_version():
    with pytest.raises(ValidationError):
        V3ContinuityStateRecord(project_id=1, shot_id=1, version=0)
