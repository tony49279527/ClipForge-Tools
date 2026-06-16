from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


AssetType = Literal["image", "video", "audio", "document"]
PrimaryRole = Literal[
    "product_identity",
    "product_geometry",
    "material_detail",
    "installation",
    "first_frame",
    "last_frame",
    "environment",
    "style",
    "motion",
    "camera",
    "timing",
    "audio",
    "continuity_anchor",
    "fact_evidence",
]


class AssetAuditWarning(BaseModel):
    code: str = Field(min_length=2, max_length=80)
    severity: Literal["info", "warning", "high"]
    message: str = Field(min_length=3, max_length=500)


class AssetAuditReport(BaseModel):
    width: int = Field(ge=0)
    height: int = Field(ge=0)
    format: str = Field(min_length=1, max_length=20)
    file_size_bytes: int = Field(ge=0)
    is_clear: bool
    has_transparent_background: bool
    has_perspective_distortion: bool
    key_structure_visible: bool
    conflict_detected: bool
    warnings: list[AssetAuditWarning] = Field(default_factory=list)
    missing_project_angles: list[str] = Field(default_factory=list)


class V3AssetRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: int
    asset_type: AssetType
    original_filename: str = Field(min_length=1, max_length=255)
    local_path: Optional[str] = None
    remote_url: Optional[str] = None
    mime_type: Optional[str] = None
    primary_role: PrimaryRole
    secondary_role: Optional[PrimaryRole] = None
    must_transfer_json: list[str] = Field(default_factory=list)
    must_not_transfer_json: list[str] = Field(default_factory=list)
    applies_to_shots_json: list[str] = Field(default_factory=list)
    is_identity_anchor: bool = False
    user_approved: bool = False
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    audit_report: AssetAuditReport

    @field_validator("original_filename", mode="before")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("original_filename must be a string")
        value = value.strip()
        if not value:
            raise ValueError("original_filename cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_role_load(self):
        if self.secondary_role and self.secondary_role == self.primary_role:
            raise ValueError("secondary_role must differ from primary_role")
        return self


class ReferenceRoleAssignment(BaseModel):
    asset_id: int = Field(ge=1)
    primary_role: PrimaryRole
    must_transfer: list[str] = Field(default_factory=list)
    must_not_transfer: list[str] = Field(default_factory=list)


class ReferenceRoleMap(BaseModel):
    assets: list[ReferenceRoleAssignment] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
