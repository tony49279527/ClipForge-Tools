from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ImmutableGeometry(BaseModel):
    shape: str = ""
    diameter: str = ""
    thickness: str = ""
    center_hole: str = ""
    component_count: str = ""
    other: list[str] = Field(default_factory=list)


class MaterialsSpec(BaseModel):
    correct: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)


class InstallationSpec(BaseModel):
    required_steps: list[str] = Field(default_factory=list)
    required_relationships: list[str] = Field(default_factory=list)
    forbidden_relationships: list[str] = Field(default_factory=list)


class WorkingSurfaceSpec(BaseModel):
    correct: list[str] = Field(default_factory=list)
    incorrect: list[str] = Field(default_factory=list)


class SourceEvidence(BaseModel):
    source_type: Literal["description", "manual_input", "product_url", "reference_asset"]
    detail: str = Field(min_length=2, max_length=500)


class ProductTruthPayload(BaseModel):
    product_type: str = Field(min_length=2, max_length=120)
    immutable_geometry: ImmutableGeometry = Field(default_factory=ImmutableGeometry)
    materials: MaterialsSpec = Field(default_factory=MaterialsSpec)
    colors: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    installation: InstallationSpec = Field(default_factory=InstallationSpec)
    working_surface: WorkingSurfaceSpec = Field(default_factory=WorkingSurfaceSpec)
    allowed_behaviors: list[str] = Field(default_factory=list)
    forbidden_transformations: list[str] = Field(default_factory=list)
    safety_constraints: list[str] = Field(default_factory=list)
    uncertain_facts: list[str] = Field(default_factory=list)
    source_evidence: list[SourceEvidence] = Field(default_factory=list)


class ProductTruthInput(BaseModel):
    product_name: str = Field(min_length=2, max_length=120)
    product_category: str = Field(min_length=2, max_length=120)
    source_description: str = Field(min_length=10, max_length=5000)
    product_url: str = Field(default="", max_length=500)
    dimensions: str = Field(default="", max_length=500)
    materials: str = Field(default="", max_length=500)
    package_quantity: str = Field(default="", max_length=120)
    parts_summary: str = Field(default="", max_length=1000)
    installation_method: str = Field(default="", max_length=1000)
    working_surface: str = Field(default="", max_length=500)
    intended_for: str = Field(default="", max_length=500)
    not_for: str = Field(default="", max_length=500)
    safety_notes: str = Field(default="", max_length=1000)

    @field_validator("*", mode="before")
    @classmethod
    def normalize_text(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value


class V3ProductTruthRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: int
    source_description: str = Field(min_length=10)
    product_truth_json: ProductTruthPayload
    user_approved: bool = False
    version: int = Field(default=1, ge=1)
    invalidates_shots: bool = False

