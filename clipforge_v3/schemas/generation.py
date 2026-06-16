from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


PromptMode = Literal["T2V", "I2V", "V2V", "R2V", "FLF2V", "edit", "extend"]
Severity = Literal["blocking_error", "warning", "suggestion"]
GenerationTier = Literal["draft", "production"]


class PromptLintIssue(BaseModel):
    severity: Severity
    code: str = Field(min_length=2, max_length=80)
    message: str = Field(min_length=3, max_length=500)
    zh: str = ""
    en: str = ""
    fix: str = ""


class PromptCompileResult(BaseModel):
    mode: PromptMode
    raw_draft_prompt: str
    anti_slop_prompt: str
    compressed_prompt: str
    final_prompt: str
    prompt_char_count: int = Field(ge=0)
    removed_items: list[str] = Field(default_factory=list)
    role_map_json: dict[str, Any] = Field(default_factory=dict)
    provider_payload_json: dict[str, Any] = Field(default_factory=dict)
    compiler_warnings_json: list[str] = Field(default_factory=list)
    lint_issues: list[PromptLintIssue] = Field(default_factory=list)
    validation_result_json: dict[str, Any] = Field(default_factory=dict)
    allow_submit: bool = False


class PreflightCheckItem(BaseModel):
    name: str
    passed: bool
    severity: Severity = "blocking_error"
    message: str


class PreflightResult(BaseModel):
    allow_submit: bool
    tier: GenerationTier
    items: list[PreflightCheckItem] = Field(default_factory=list)
    degraded_roles: list[str] = Field(default_factory=list)
    fail_closed_roles: list[str] = Field(default_factory=list)
    fail_open_roles: list[str] = Field(default_factory=list)


class V3PromptVersionRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    shot_id: int
    version: int = Field(ge=1)
    mode: PromptMode
    prompt_text: str = Field(min_length=20)
    prompt_char_count: int = Field(ge=20)
    prompt_language: str = Field(min_length=2, max_length=20)
    role_map_json: dict[str, Any] = Field(default_factory=dict)
    compiler_warnings_json: list[str] = Field(default_factory=list)
    validation_result_json: dict[str, Any] = Field(default_factory=dict)
    raw_draft_prompt: str = ""
    anti_slop_prompt: str = ""
    compressed_prompt: str = ""
    removed_items_json: list[str] = Field(default_factory=list)
    provider_payload_json: dict[str, Any] = Field(default_factory=dict)
    allow_submit: bool = False
    locked_by_user: bool = False
    created_at: Optional[str] = None


class V3TakeRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    shot_id: int
    take_number: int = Field(ge=1)
    prompt_version_id: int = Field(ge=1)
    seedance_task_id: Optional[str] = None
    status: str = Field(default="queued", min_length=2, max_length=40)
    local_path: Optional[str] = None
    remote_url: Optional[str] = None
    first_frame_path: Optional[str] = None
    last_frame_path: Optional[str] = None
    seed: Optional[int] = Field(default=None, ge=0)
    generation_settings_json: dict[str, Any] = Field(default_factory=dict)
    changed_variable: Optional[str] = Field(default=None, max_length=80)
    previous_value: Optional[str] = None
    new_value: Optional[str] = None
    change_reason: Optional[str] = Field(default=None, max_length=1200)
    source_asset_ids_json: list[int] = Field(default_factory=list)
    qc_frame_paths_json: list[str] = Field(default_factory=list)
    selected_by_user: bool = False
    uncontrolled_revision: bool = False
    deleted_local_file: bool = False
    parent_take_id: Optional[int] = Field(default=None, ge=1)
    token_usage: int = Field(default=0, ge=0)
    estimated_cost: float = Field(default=0, ge=0)
    tier: GenerationTier = "draft"


class FinalAssemblyRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: int = Field(ge=1)
    version: int = Field(ge=1)
    status: str = Field(min_length=2, max_length=40)
    output_path: Optional[str] = None
    assembly_take_ids_json: list[int] = Field(default_factory=list)
    invalidated: bool = False
