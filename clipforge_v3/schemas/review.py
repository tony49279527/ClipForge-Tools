from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


ReviewVerdict = Literal["KEEP", "FIX_IN_POST", "EDIT", "REROLL", "REWRITE"]
SafetyVerdict = Literal["pass", "fail"]


class V3ReviewRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    take_id: int = Field(ge=1)
    verdict: ReviewVerdict
    product_identity_score: int = Field(ge=0, le=10)
    mechanical_accuracy_score: int = Field(ge=0, le=10)
    material_accuracy_score: int = Field(ge=0, le=10)
    motion_realism_score: int = Field(ge=0, le=10)
    camera_execution_score: int = Field(ge=0, le=10)
    continuity_score: int = Field(ge=0, le=10)
    commercial_usability_score: int = Field(ge=0, le=10)
    safety: SafetyVerdict = "pass"
    error_codes_json: list[str] = Field(default_factory=list)
    reviewer_notes: Optional[str] = Field(default=None, max_length=3000)
    next_action: str = Field(min_length=2, max_length=120)
    ai_suggestion_json: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None


class RetakePlan(BaseModel):
    verdict: ReviewVerdict
    root_cause: str = Field(min_length=3, max_length=500)
    changed_variable: str = Field(min_length=2, max_length=120)
    prompt_patch: str = Field(default="", max_length=1200)
    reference_change: Optional[str] = Field(default=None, max_length=500)
    mode_change: Optional[str] = Field(default=None, max_length=80)
    requires_new_shot: bool = False
    estimated_next_cost: float = Field(default=0, ge=0)
    reason: str = Field(min_length=3, max_length=1200)
    warnings: list[str] = Field(default_factory=list)
