from __future__ import annotations

from dataclasses import dataclass
import re

from clipforge_v3.providers.config import SEEDANCE_PROMPT_MAX_CHARS
from clipforge_v3.schemas.generation import PromptCompileResult, PromptLintIssue


GENERIC_QUALITY_WORDS = ["cinematic", "epic", "stunning", "masterpiece", "beautiful", "professional quality", "highly dynamic", "8k", "award-winning"]
ACTION_CONNECTORS = re.compile(r"\b(then|after that|while|and then|simultaneously|also)\b", re.IGNORECASE)
CAMERA_MOVES = {"pan", "tilt", "dolly", "push", "pull", "orbit", "crane", "zoom", "tracking", "handheld"}
READABLE_TEXT_TERMS = {"readable text", "small text", "serial number", "price", "qr code", "cta", "model number"}


@dataclass
class CompilerInput:
    project: dict
    shot: dict
    product_truth: dict
    role_map: dict
    continuity_state: dict
    mode: str
    provider_capabilities: dict
    user_constraints: list[str]


def normalize_input(inp: CompilerInput) -> CompilerInput:
    inp.user_constraints = [item.strip() for item in inp.user_constraints if item.strip()]
    return inp


def select_mode_template(inp: CompilerInput) -> str:
    base = {
        "T2V": "Subject -> Primary action -> Timing -> Camera -> Physical lighting -> Audio -> Reference roles -> Preservation constraints -> Forbidden errors -> Endpoint",
        "I2V": "Preserve image-anchored product identity. Describe only motion, timing, camera, light change, sound, endpoint, and shape protection.",
        "R2V": "[Image1] controls product identity. [Image2] controls installation geometry. [Video1] controls motion only.",
        "FLF2V": "Image1 is the first frame. Image2 is the target final frame. Generate only the continuous transition between them.",
        "V2V": "Source video controls motion timing. Preserve only explicitly allowed layers.",
        "edit": "Modify one layer only.",
        "extend": "Continue from the previous final state without resetting scene.",
    }
    return base[inp.mode]


def inject_product_constraints(inp: CompilerInput, text: str) -> str:
    truth = inp.product_truth["product_truth_json"]
    geometry = truth["immutable_geometry"]
    geometry_summary = ", ".join(
        item
        for item in [
            geometry.get("shape"),
            geometry.get("diameter"),
            geometry.get("thickness"),
            geometry.get("center_hole"),
            geometry.get("component_count"),
        ]
        if item
    )
    return (
        f"{text} Subject: {inp.project['product_name']}. Preserve geometry {geometry_summary}. "
        f"Correct materials: {', '.join(truth['materials']['correct'])}. Forbidden materials: {', '.join(truth['materials']['forbidden'])}. "
        f"Working surface: {', '.join(truth['working_surface']['correct'])}. Forbidden transformations: {'; '.join(truth['forbidden_transformations'])}."
    )


def inject_reference_role_map(inp: CompilerInput, text: str) -> str:
    refs = []
    for idx, asset in enumerate(inp.role_map["assets"], start=1):
        refs.append(f"[Asset{idx}] controls {asset['primary_role']}; transfer {asset['must_transfer']}; exclude {asset['must_not_transfer']}.")
    return f"{text} Reference roles: {' '.join(refs)}"


def inject_continuity_anchors(inp: CompilerInput, text: str) -> str:
    anchors = inp.shot.get("continuity_anchors_json", {})
    parts = []
    if anchors:
        parts.append(f"anchors {anchors}")
    if inp.continuity_state:
        ledger = inp.continuity_state
        product = ledger.get("product_state_json", {})
        machine = ledger.get("machine_state_json", {})
        camera = ledger.get("camera_state_json", {})
        lighting = ledger.get("lighting_state_json", {})
        summary_parts = [
            f"product orientation {product.get('orientation', '')}",
            f"product position {product.get('position', '')}",
            f"machine power {machine.get('power', '')}",
            f"spindle {machine.get('spindle_direction', '')}",
            f"camera side {camera.get('side', '')}",
            f"camera framing {camera.get('framing', '')}",
            f"light key {lighting.get('key_direction', '') or lighting.get('key', '')}",
        ]
        parts.append(f"previous {'; '.join(item for item in summary_parts if item.strip())}")
    return f"{text} Continuity: {'; '.join(parts)}." if parts else text


def build_director_prompt(inp: CompilerInput, text: str) -> str:
    shot = inp.shot
    camera = shot["camera_contract_json"]
    lighting = shot["lighting_contract_json"]
    audio = shot["audio_contract_json"]
    endpoint = ", ".join(f"{k}:{v}" for k, v in shot["end_state_json"].items())
    constraints = "; ".join(shot["constraints_json"])
    return (
        f"{text} Subject {inp.project['product_name']}. Primary action {shot['subject_action']}. Timing {shot['duration']} seconds. "
        f"Camera framing {camera.get('framing', '')}, movement {camera.get('movement', '')}, endpoint {camera.get('endpoint', '')}. "
        f"Lighting {', '.join(f'{k}:{v}' for k, v in lighting.items())}. Audio {', '.join(f'{k}:{v}' for k, v in audio.items())}. "
        f"Endpoint {endpoint}. Constraints {constraints}."
    )


def anti_slop_pass(text: str) -> tuple[str, list[str]]:
    removed = []
    output = text
    for word in GENERIC_QUALITY_WORDS:
        if word.lower() in output.lower():
            removed.append(f"removed_generic:{word}")
            output = output.replace(word, "").replace(word.title(), "")
    return " ".join(output.split()), removed


def detect_conflicts(inp: CompilerInput, text: str) -> list[PromptLintIssue]:
    issues: list[PromptLintIssue] = []
    if not inp.product_truth or not inp.product_truth.get("user_approved"):
        issues.append(PromptLintIssue(severity="blocking_error", code="PRODUCT_IDENTITY_NOT_CONFIRMED", message="Product Truth is not confirmed.", zh="产品事实尚未确认。", en="Product Truth is not confirmed.", fix="Confirm Product Truth before compiling or generating."))
    if not inp.provider_capabilities.get("supported", False):
        issues.append(PromptLintIssue(severity="blocking_error", code="UNSUPPORTED_PROVIDER_MODE", message="Provider does not support the selected mode or required references.", zh="Provider 不支持当前模式或素材要求。", en="Provider does not support the selected mode or required references.", fix="Change mode or provide required assets."))
    missing = inp.provider_capabilities.get("missing", [])
    if missing:
        issues.append(PromptLintIssue(severity="blocking_error", code="MISSING_REQUIRED_REFERENCE", message=f"Missing required references: {', '.join(missing)}.", zh="缺少必需参考素材。", en="Missing required references.", fix="Upload and confirm required reference assets."))
    if len(inp.shot.get("risk_codes_json", [])) > 2:
        issues.append(PromptLintIssue(severity="warning", code="FIDELITY_OVERLOAD", message="Shot already carries multiple risks.", zh="镜头风险过载。", en="Shot already carries multiple risks.", fix="Split the shot or reduce scene density."))
    if inp.mode == "I2V" and "overall geometry" not in text:
        issues.append(PromptLintIssue(severity="blocking_error", code="MISSING_REQUIRED_REFERENCE", message="I2V prompt is missing explicit identity preservation.", zh="I2V 缺少产品身份保护。", en="I2V prompt is missing explicit identity preservation.", fix="Add product_identity reference role with overall geometry in must_transfer."))
    if inp.mode == "I2V" and len(str(inp.product_truth.get("product_truth_json", {}))) > 2500:
        issues.append(PromptLintIssue(severity="warning", code="I2V_OVER_DESCRIPTION", message="I2V may be over-describing static product facts.", zh="I2V 可能过度描述静态产品。", en="I2V may be over-describing static product facts.", fix="Keep identity in references and describe only motion/change."))
    normalized = text.lower()
    normalized = re.sub(r"forbidden materials\s*:\s*[^.;]+", " ", normalized)
    normalized = re.sub(
        r"(do not change to|do not turn into|must not become|must not use|avoid|exclude|forbidden)\s+[^.;]+",
        " ",
        normalized,
    )
    forbidden_materials = inp.product_truth["product_truth_json"]["materials"]["forbidden"]
    for material in forbidden_materials:
        if re.search(rf"\b{re.escape(material.lower())}\b", normalized):
            issues.append(PromptLintIssue(severity="blocking_error", code="FORBIDDEN_MATERIAL_CONFLICT", message=f"Prompt conflicts with forbidden material {material}.", zh="Prompt 与禁止材质冲突。", en="Prompt conflicts with forbidden material.", fix="Remove forbidden material or rewrite as explicit negative constraint."))
    subject_action = str(inp.shot.get("subject_action", ""))
    if ACTION_CONNECTORS.search(subject_action):
        issues.append(PromptLintIssue(severity="warning", code="MULTIPLE_PRIMARY_ACTIONS", message="Subject action appears to contain multiple action phases.", zh="镜头动作可能包含多个阶段。", en="Subject action appears to contain multiple action phases.", fix="Split into separate shots or keep one visible beat."))
    text_lower = text.lower()
    if any(term in text_lower for term in READABLE_TEXT_TERMS):
        issues.append(PromptLintIssue(severity="warning", code="READABLE_TEXT_REQUEST", message="Readable dense text should be handled in post.", zh="密集可读文字应交给后期。", en="Readable dense text should be handled in post.", fix="Remove model-generated text request and add it in post."))
    if any(term in text_lower for term in {"hand near spinning", "bare hand contacts blade", "unsafe guard removed"}):
        issues.append(PromptLintIssue(severity="blocking_error", code="UNSAFE_MECHANICAL_ACTION", message="Prompt asks for unsafe mechanical action.", zh="Prompt 要求不安全机械动作。", en="Prompt asks for unsafe mechanical action.", fix="Rewrite with safe hand placement and guarded operation."))
    roles = [asset.get("primary_role") for asset in inp.role_map.get("assets", [])]
    if len(roles) != len(set(roles)) and "product_identity" in roles:
        issues.append(PromptLintIssue(severity="warning", code="REFERENCE_ROLE_CONFLICT", message="Multiple assets share a primary role; verify role downgrade.", zh="多个素材共用主职责。", en="Multiple assets share a primary role.", fix="Keep one primary identity asset and downgrade optional references."))
    if "complex workshop" in text_lower or "crowded" in text_lower:
        issues.append(PromptLintIssue(severity="warning", code="SCENE_TOO_DENSE", message="Scene may be too dense for product identity fidelity.", zh="场景过密可能影响产品身份。", en="Scene may be too dense for product identity fidelity.", fix="Economize background and human activity."))
    return issues


def enforce_single_visible_beat(inp: CompilerInput, text: str) -> tuple[str, list[PromptLintIssue]]:
    issues: list[PromptLintIssue] = []
    beat = str(inp.shot["single_visible_beat"]).lower()
    action_count = sum(1 for term in ["install", "start", "polish", "result", "reveal", "tighten", "remove"] if term in beat)
    if ACTION_CONNECTORS.search(beat) or action_count > 1:
        issues.append(PromptLintIssue(severity="warning", code="MULTIPLE_PRIMARY_ACTIONS", message="Shot beat appears to contain multiple visible beats.", zh="镜头可见节拍过多。", en="Shot beat appears to contain multiple visible beats.", fix="Keep exactly one primary visible beat."))
    return text, issues


def enforce_single_camera_move(inp: CompilerInput, text: str) -> tuple[str, list[PromptLintIssue]]:
    issues: list[PromptLintIssue] = []
    movement = str(inp.shot["camera_contract_json"].get("movement", "")).lower()
    move_hits = [move for move in CAMERA_MOVES if move in movement]
    if len(move_hits) > 1 or ACTION_CONNECTORS.search(movement) or "," in movement:
        issues.append(PromptLintIssue(severity="warning", code="CAMERA_OVERLOAD", message="Camera contract suggests multiple movements.", zh="镜头运动过载。", en="Camera contract suggests multiple movements.", fix="Keep one main camera movement."))
        issues.append(PromptLintIssue(severity="warning", code="multiple_camera_moves", message="Camera contract suggests multiple movements."))
    return text, issues


def compress_to_budget(inp: CompilerInput, text: str) -> tuple[str, list[str], bool]:
    removed = []
    compressed = text
    def _remove_generic_quality_words(value: str) -> str:
        output = value
        for word in GENERIC_QUALITY_WORDS:
            output = output.replace(word, "").replace(word.title(), "").replace(word.upper(), "")
        return output

    rules = [
        ("deleted duplicate adjectives", lambda t: t.replace(" very ", " ").replace(" extremely ", " ")),
        ("deleted generic quality words", _remove_generic_quality_words),
        ("deleted background description", lambda t: t.replace(" background ", " ")),
        ("deleted second camera move", lambda t: t.replace(" then pan", "")),
        ("deleted secondary action", lambda t: t.replace(" and secondary action", "")),
        ("shortened lighting/audio", lambda t: t.replace(" Physical lighting ", " Lighting ").replace(" Audio ", " Audio ")),
        ("compressed endpoint and constraints", lambda t: t.replace("Constraints [", "Constraints ").replace("Endpoint {", "Endpoint ").replace("}. Constraints", ". Constraints").replace("]", "")),
    ]
    for label, fn in rules:
        if len(compressed) <= SEEDANCE_PROMPT_MAX_CHARS:
            break
        new_text = " ".join(fn(compressed).split())
        if new_text != compressed:
            removed.append(label)
            compressed = new_text
    return compressed, removed, len(compressed) <= SEEDANCE_PROMPT_MAX_CHARS


def validate_final_prompt(inp: CompilerInput, text: str, issues: list[PromptLintIssue]) -> list[PromptLintIssue]:
    if len(text) > SEEDANCE_PROMPT_MAX_CHARS:
        issues.append(PromptLintIssue(severity="blocking_error", code="PROMPT_OVER_BUDGET", message="Prompt exceeds provider character limit and cannot be safely compressed.", zh="Prompt 超过字符限制。", en="Prompt exceeds provider character limit.", fix="Compress or split the shot."))
        issues.append(PromptLintIssue(severity="blocking_error", code="prompt_over_budget", message="Prompt exceeds provider character limit and cannot be safely compressed."))
    if "endpoint" not in text.lower() and "end state" not in text.lower():
        issues.append(PromptLintIssue(severity="warning", code="MISSING_ENDPOINT", message="Prompt is missing an explicit endpoint.", zh="缺少明确终点。", en="Prompt is missing an explicit endpoint.", fix="Add endpoint/end state."))
    if "product identity" not in text.lower() and inp.mode in {"I2V", "R2V", "FLF2V"}:
        issues.append(PromptLintIssue(severity="blocking_error", code="MISSING_REQUIRED_REFERENCE", message="Identity-preserving mode is missing a product identity clause.", zh="身份保护模式缺少产品身份条款。", en="Identity-preserving mode is missing a product identity clause.", fix="Add product identity preservation clause."))
    return issues
