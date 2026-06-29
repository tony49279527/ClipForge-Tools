from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


SeedanceMode = Literal["T2V", "I2V", "V2V", "R2V", "FLF2V", "edit", "extend"]
BudgetType = Literal["product_identity", "motion_boldness", "scene_density", "material_detail", "material_motion"]
RiskLevel = Literal["low", "medium", "high"]


class ModeDecision(BaseModel):
    selected_mode: SeedanceMode
    reason: str = Field(min_length=3, max_length=1000)
    alternative_mode: SeedanceMode
    required_assets: list[str] = Field(default_factory=list)
    missing_assets: list[str] = Field(default_factory=list)
    continuity_required: bool = False
    generation_strategy: Literal["parallel", "sequential", "linked_retry"] = "parallel"
    risk_level: RiskLevel = "medium"


class FidelityAllocation(BaseModel):
    primary_spend: BudgetType
    secondary_spend: Optional[BudgetType] = None
    economized: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=3, max_length=500)
    split_recommended: bool = False
    split_reason: str = ""


class CameraContract(BaseModel):
    framing: str = ""
    height: str = ""
    angle: str = ""
    movement: str = ""
    endpoint: str = ""


class ShotContractPayload(BaseModel):
    shot_id: str = Field(min_length=2, max_length=32)
    purpose: str = Field(min_length=3, max_length=120)
    commercial_beat: str = Field(default="", max_length=200)
    duration: int = Field(ge=4, le=8)
    mode: SeedanceMode
    primary_spend: BudgetType
    secondary_spend: Optional[BudgetType] = None
    economized: list[str] = Field(default_factory=list)
    subject_action: str = Field(min_length=3, max_length=300)
    single_visible_beat: str = Field(min_length=3, max_length=200)
    start_state: dict[str, Any] = Field(default_factory=dict)
    end_state: dict[str, Any] = Field(default_factory=dict)
    camera_contract: CameraContract = Field(default_factory=CameraContract)
    lighting_contract: dict[str, Any] = Field(default_factory=dict)
    audio_contract: dict[str, Any] = Field(default_factory=dict)
    reference_roles: list[dict[str, Any]] = Field(default_factory=list)
    continuity_anchors: dict[str, Any] = Field(default_factory=dict)
    continuity_group: str = Field(default="default", min_length=2, max_length=120)
    constraints: list[str] = Field(default_factory=list)
    risk_codes: list[str] = Field(default_factory=list)
    generation_strategy: Literal["parallel", "sequential", "linked_retry"] = "parallel"
    depends_on_shot_id: Optional[str] = None


class V3ShotRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: int
    shot_id: str = Field(min_length=2, max_length=32)
    sequence_index: int = Field(ge=1, le=99)
    purpose: str = Field(min_length=3, max_length=200)
    commercial_beat: str = Field(default="", max_length=200)
    duration: int = Field(ge=4, le=8)
    mode: SeedanceMode
    primary_spend: BudgetType
    secondary_spend: Optional[BudgetType] = None
    economized_json: list[str] = Field(default_factory=list)
    subject_action: str = Field(min_length=3, max_length=300)
    single_visible_beat: str = Field(min_length=3, max_length=200)
    start_state_json: dict[str, Any] = Field(default_factory=dict)
    end_state_json: dict[str, Any] = Field(default_factory=dict)
    camera_contract_json: dict[str, Any] = Field(default_factory=dict)
    lighting_contract_json: dict[str, Any] = Field(default_factory=dict)
    audio_contract_json: dict[str, Any] = Field(default_factory=dict)
    reference_roles_json: list[dict[str, Any]] = Field(default_factory=list)
    continuity_anchors_json: dict[str, Any] = Field(default_factory=dict)
    continuity_group: str = Field(default="default", min_length=2, max_length=120)
    constraints_json: list[str] = Field(default_factory=list)
    risk_codes_json: list[str] = Field(default_factory=list)
    generation_strategy: str = Field(min_length=2, max_length=120)
    depends_on_shot_id: Optional[str] = None
    status: str = Field(default="planned", min_length=2, max_length=40)
    user_approved: bool = False
    version: int = Field(default=1, ge=1)
    mode_decision_json: dict[str, Any] = Field(default_factory=dict)
    fidelity_json: dict[str, Any] = Field(default_factory=dict)
    locked_by_user: bool = False
    selected_take_id: Optional[int] = Field(default=None, ge=1)

    @field_validator(
        "shot_id",
        "purpose",
        "subject_action",
        "single_visible_beat",
        "generation_strategy",
        "status",
        mode="before",
    )
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("value must be a string")
        value = value.strip()
        if not value:
            raise ValueError("value cannot be blank")
        return value
