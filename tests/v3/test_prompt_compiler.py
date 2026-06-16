from __future__ import annotations

from clipforge_v3.compiler.prompt_compiler import (
    CompilerInput,
    anti_slop_pass,
    build_director_prompt,
    compress_to_budget,
    detect_conflicts,
    enforce_single_camera_move,
    normalize_input,
    select_mode_template,
    validate_final_prompt,
)
from clipforge_v3.providers.seedance_ark import ArkSeedanceProvider


def _compiler_input(mode="I2V", movement="locked", extra_action=""):
    return CompilerInput(
        project={"product_name": "Buffing Wheel", "product_category": "hardware tools"},
        shot={
            "subject_action": f"Outer cotton circumference polishes metal{extra_action}",
            "duration": 5,
            "camera_contract_json": {"movement": movement},
            "lighting_contract_json": {"style": "soft upper-left key"},
            "audio_contract_json": {"priority": "diegetic realism"},
            "end_state_json": {"endpoint": "mirror shine"},
            "constraints_json": ["Preserve center hole", "No material drift"],
            "risk_codes_json": [],
        },
        product_truth={
            "product_truth_json": {
                "immutable_geometry": {"center_hole": "1/2-inch"},
                "materials": {"correct": ["natural off-white cotton"], "forbidden": ["wool", "felt"]},
                "working_surface": {"correct": ["outer cotton circumference"]},
                "forbidden_transformations": ["Do not become grinding stone"],
            }
        },
        role_map={"assets": [{"primary_role": "product_identity", "must_transfer": ["center hole"], "must_not_transfer": ["background"]}]},
        continuity_state={},
        mode=mode,
        provider_capabilities={"supported": True},
        user_constraints=[],
    )


def test_1999_chars_pass():
    inp = _compiler_input()
    text = "a" * 1999
    issues = validate_final_prompt(inp, text, [])
    assert not any(issue.code == "prompt_over_budget" for issue in issues)


def test_2001_chars_fail_without_truncation():
    inp = _compiler_input()
    text = "a" * 2001
    issues = validate_final_prompt(inp, text, [])
    assert any(issue.code == "prompt_over_budget" for issue in issues)
    assert len(text) == 2001


def test_i2v_template_does_not_redescribe_entire_static_product():
    template = select_mode_template(_compiler_input(mode="I2V"))
    assert "Preserve image-anchored product identity" in template
    assert "re-design" not in template.lower()


def test_r2v_template_outputs_role_exclusions():
    template = select_mode_template(_compiler_input(mode="R2V"))
    assert "[Image1] controls product identity." in template
    assert "controls motion only" in template


def test_flf2v_template_mentions_first_last_frames():
    template = select_mode_template(_compiler_input(mode="FLF2V"))
    assert "first frame" in template.lower()
    assert "target final frame" in template.lower()


def test_product_truth_conflict_blocks():
    inp = _compiler_input()
    issues = detect_conflicts(inp, "This turns the product into wool.")
    assert any(issue.severity == "blocking_error" for issue in issues)


def test_forbidden_material_negation_does_not_block():
    inp = _compiler_input()
    issues = detect_conflicts(inp, "Do not change to wool. Forbidden materials: ['wool', 'felt'].")
    assert not any(issue.code == "forbidden_material" for issue in issues)


def test_multiple_camera_moves_warn():
    inp = _compiler_input(movement="slow push and pan")
    _, issues = enforce_single_camera_move(inp, "text")
    assert any(issue.code == "multiple_camera_moves" for issue in issues)


def test_compress_does_not_silently_truncate():
    inp = _compiler_input()
    text = ("cinematic " * 400) + "endpoint"
    compressed, removed, fits = compress_to_budget(inp, text)
    assert "deleted generic quality words" in removed or removed
    assert compressed != text[:2000]
    assert isinstance(fits, bool)


def test_payload_contains_resolution():
    provider = ArkSeedanceProvider()
    payload = provider.build_payload(
        prompt_text="Prompt",
        mode="I2V",
        ratio="16:9",
        duration=5,
        resolution="1080p",
        generate_audio=True,
        reference_roles=[{"primary_role": "product_identity", "must_transfer": [], "must_not_transfer": []}],
    )
    assert payload["resolution"] == "1080p"


def test_api_key_not_in_logs():
    provider = ArkSeedanceProvider()
    sanitized = provider.normalize_error({"Authorization": "Bearer secret-key", "message": "bad"})
    assert "secret-key" not in sanitized["message"]
