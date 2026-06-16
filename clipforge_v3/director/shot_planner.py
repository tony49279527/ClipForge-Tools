from __future__ import annotations

from clipforge_v3.providers.openai_text import to_json_text, validate_shot_contract_json


def default_beats(product_name: str, product_type: str) -> list[dict]:
    return [
        {
            "purpose": "product_structure_proof",
            "commercial_beat": f"Show the real structure of {product_name}.",
            "subject_action": f"{product_name} remains still while its structure is shown clearly.",
            "single_visible_beat": f"Viewer understands the structure of the {product_type}.",
            "camera": {"framing": "medium close", "height": "product height", "angle": "front three-quarter", "movement": "locked", "endpoint": "clean structure hold"},
        },
        {
            "purpose": "installation_relationship_proof",
            "commercial_beat": "Show how the product mounts or assembles.",
            "subject_action": "Assembly relationship is demonstrated with one clean mounting action.",
            "single_visible_beat": "Mounting relationship becomes obvious.",
            "camera": {"framing": "tight close", "height": "spindle level", "angle": "side/front", "movement": "slow push", "endpoint": "mounting relationship held clearly"},
        },
        {
            "purpose": "working_surface_proof",
            "commercial_beat": "Show the correct working contact area.",
            "subject_action": "The tool contacts the correct working surface only.",
            "single_visible_beat": "Correct working surface is unmistakable.",
            "camera": {"framing": "close action detail", "height": "contact point", "angle": "side", "movement": "locked", "endpoint": "contact maintained"},
        },
        {
            "purpose": "result_proof",
            "commercial_beat": "Show visible result after correct use.",
            "subject_action": "Result state is revealed after the prior action completes.",
            "single_visible_beat": "Commercial payoff is visible.",
            "camera": {"framing": "medium result shot", "height": "product level", "angle": "front", "movement": "slow reveal", "endpoint": "result hold"},
        },
    ]


def build_shot_contracts(*, product_name: str, product_type: str, truth: dict, role_maps: list[dict], mode_decisions: list[dict], fidelities: list[dict]) -> list[dict]:
    beats = default_beats(product_name, product_type)
    contracts: list[dict] = []
    for index, beat in enumerate(beats, start=1):
        payload = {
            "shot_id": f"S{index:02d}",
            "purpose": beat["purpose"],
            "commercial_beat": beat["commercial_beat"],
            "duration": 5,
            "mode": mode_decisions[index - 1]["selected_mode"],
            "primary_spend": fidelities[index - 1]["primary_spend"],
            "secondary_spend": fidelities[index - 1].get("secondary_spend"),
            "economized": fidelities[index - 1]["economized"],
            "subject_action": beat["subject_action"],
            "single_visible_beat": beat["single_visible_beat"],
            "start_state": {
                "visible_components": truth["product_truth_json"]["components"],
                "working_surface": truth["product_truth_json"]["working_surface"]["correct"],
            },
            "end_state": {
                "visible_components": truth["product_truth_json"]["components"],
                "result_ready": beat["purpose"] == "result_proof",
            },
            "camera_contract": beat["camera"],
            "lighting_contract": {"style": "stable practical lighting"},
            "audio_contract": {"priority": "diegetic realism"},
            "reference_roles": role_maps[index - 1]["assets"],
            "continuity_anchors": {
                "product_type": truth["product_truth_json"]["product_type"],
                "geometry": truth["product_truth_json"]["immutable_geometry"],
            },
            "constraints": [
                "Only one main visible action.",
                "Do not rely on model-generated text overlays.",
            ],
            "risk_codes": [mode_decisions[index - 1]["risk_level"]] + role_maps[index - 1]["warnings"],
            "generation_strategy": mode_decisions[index - 1]["generation_strategy"],
            "depends_on_shot_id": None if index == 1 else f"S{index - 1:02d}",
        }
        contracts.append(validate_shot_contract_json(to_json_text(payload)).model_dump())
    return contracts
