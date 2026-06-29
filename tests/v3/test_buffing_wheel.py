from __future__ import annotations

from clipforge_v3.services.product_truth_service import extract_product_truth_payload
from clipforge_v3.schemas.product_truth import ProductTruthInput
from clipforge_v3.compiler.prompt_compiler import CompilerInput, build_director_prompt, inject_product_constraints, select_mode_template


def test_buffing_wheel_product_truth(buffing_wheel_payload):
    payload = extract_product_truth_payload(
        ProductTruthInput(
            product_name=buffing_wheel_payload["product_name"],
            product_category=buffing_wheel_payload["product_category"],
            source_description=buffing_wheel_payload["source_description"],
            product_url=buffing_wheel_payload["product_url"],
            dimensions=buffing_wheel_payload["dimensions_input"],
            materials=buffing_wheel_payload["materials_input"],
            package_quantity=buffing_wheel_payload["package_quantity"],
            parts_summary=buffing_wheel_payload["parts_summary"],
            installation_method=buffing_wheel_payload["installation_method"],
            working_surface=buffing_wheel_payload["working_surface_input"],
            intended_for=buffing_wheel_payload["intended_for"],
            not_for=buffing_wheel_payload["not_for"],
            safety_notes=buffing_wheel_payload["safety_notes"],
        )
    )
    assert payload["immutable_geometry"]["shape"] == "circular wheel"
    assert payload["immutable_geometry"]["thickness"] == "1-inch"
    assert payload["immutable_geometry"]["center_hole"] == "1/2-inch"
    assert "natural off-white cotton" in payload["materials"]["correct"]
    assert "wool" in " ".join(payload["materials"]["forbidden"])
    assert any("approximate" in item.lower() for item in payload["uncertain_facts"])
    assert "outer cotton circumference" in payload["working_surface"]["correct"][0]


def test_product_truth_geometry_recognizes_common_hole_and_thickness_forms(buffing_wheel_payload):
    cases = [
        ("6-inch diameter, 1 inch thick, 1/2\" arbor hole", "1-inch", "1/2-inch"),
        ("6-inch diameter, thickness: 1 in, 0.5 inch bore", "1-inch", "0.5-inch"),
        ("6-inch diameter, 1-inch thickness, center hole: 12.7 mm", "1-inch", "center hole: 12.7 mm"),
    ]
    for dimensions, expected_thickness, expected_hole in cases:
        payload = extract_product_truth_payload(
            ProductTruthInput(
                product_name=buffing_wheel_payload["product_name"],
                product_category=buffing_wheel_payload["product_category"],
                source_description="Geometry-only measurement test case.",
                product_url=buffing_wheel_payload["product_url"],
                dimensions=dimensions,
                materials=buffing_wheel_payload["materials_input"],
                package_quantity=buffing_wheel_payload["package_quantity"],
                parts_summary=buffing_wheel_payload["parts_summary"],
                installation_method=buffing_wheel_payload["installation_method"],
                working_surface=buffing_wheel_payload["working_surface_input"],
                intended_for=buffing_wheel_payload["intended_for"],
                not_for=buffing_wheel_payload["not_for"],
                safety_notes=buffing_wheel_payload["safety_notes"],
            )
        )
        assert payload["immutable_geometry"]["thickness"] == expected_thickness
        assert payload["immutable_geometry"]["center_hole"] == expected_hole


def test_buffing_wheel_prompt_keeps_core_facts(buffing_wheel_payload):
    truth = extract_product_truth_payload(
        ProductTruthInput(
            product_name=buffing_wheel_payload["product_name"],
            product_category=buffing_wheel_payload["product_category"],
            source_description=buffing_wheel_payload["source_description"],
            product_url=buffing_wheel_payload["product_url"],
            dimensions=buffing_wheel_payload["dimensions_input"],
            materials=buffing_wheel_payload["materials_input"],
            package_quantity=buffing_wheel_payload["package_quantity"],
            parts_summary=buffing_wheel_payload["parts_summary"],
            installation_method=buffing_wheel_payload["installation_method"],
            working_surface=buffing_wheel_payload["working_surface_input"],
            intended_for=buffing_wheel_payload["intended_for"],
            not_for=buffing_wheel_payload["not_for"],
            safety_notes=buffing_wheel_payload["safety_notes"],
        )
    )
    inp = CompilerInput(
        project={"product_name": buffing_wheel_payload["product_name"], "product_category": buffing_wheel_payload["product_category"]},
        shot={
            "subject_action": "The outer cotton circumference polishes metal while the center hole remains fixed on the spindle.",
            "duration": 5,
            "camera_contract_json": {"movement": "locked"},
            "lighting_contract_json": {},
            "audio_contract_json": {},
            "end_state_json": {"result": "polished metal"},
            "constraints_json": ["Preserve center hole", "Keep natural cotton material", "Show correct installation relationship"],
        },
        product_truth={"product_truth_json": truth},
        role_map={"assets": []},
        continuity_state={},
        mode="I2V",
        provider_capabilities={"supported": True},
        user_constraints=[],
    )
    text = build_director_prompt(inp, inject_product_constraints(inp, select_mode_template(inp)))
    assert "center_hole" in text or "center hole" in text
    assert "natural off-white cotton" in text
    assert "outer cotton circumference" in text
