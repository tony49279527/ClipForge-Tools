from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


AllowedPlatform = Literal["amazon", "youtube", "tiktok", "instagram", "shopify", "multi_platform"]
AllowedRatio = Literal["16:9", "9:16", "1:1", "4:5"]
AllowedResolution = Literal["720p", "1080p", "4k"]
AllowedLanguage = Literal["en", "zh", "bilingual"]


class V3ProjectCreate(BaseModel):
    project_name: str = Field(min_length=3, max_length=120)
    product_name: str = Field(min_length=2, max_length=120)
    product_category: str = Field(min_length=2, max_length=80)
    target_market: str = Field(default="US", min_length=2, max_length=80)
    target_audience: str = Field(min_length=3, max_length=300)
    target_platform: AllowedPlatform = "amazon"
    aspect_ratio: AllowedRatio = "16:9"
    total_duration: int = Field(default=30, ge=6, le=300)
    default_clip_duration: int = Field(default=6, ge=2, le=30)
    resolution: AllowedResolution = "1080p"
    language: AllowedLanguage = "en"
    source_description: str = Field(min_length=10, max_length=5000)
    product_url: str = Field(default="", max_length=500)
    dimensions_input: str = Field(default="", max_length=500)
    materials_input: str = Field(default="", max_length=500)
    package_quantity: str = Field(default="", max_length=120)
    parts_summary: str = Field(default="", max_length=1000)
    installation_method: str = Field(default="", max_length=1000)
    working_surface_input: str = Field(default="", max_length=500)
    intended_for: str = Field(default="", max_length=500)
    not_for: str = Field(default="", max_length=500)
    safety_notes: str = Field(default="", max_length=1000)

    @field_validator(
        "*",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value):
        if not isinstance(value, str):
            return value
        value = value.strip()
        return value


class V3ProjectRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_name: str
    product_name: str
    product_category: str
    target_market: str
    target_audience: str
    target_platform: AllowedPlatform
    aspect_ratio: AllowedRatio
    total_duration: int
    default_clip_duration: int
    resolution: AllowedResolution
    language: AllowedLanguage
    project_status: str
    current_stage: str
    created_at: str
    updated_at: str


class V3ProjectStatus(BaseModel):
    project_id: int
    project_status: str
    current_stage: str
    step_counts: dict[str, int]
    latest_prompt_version_count: int = 0
    latest_take_count: int = 0
    latest_review_count: int = 0
    latest_continuity_state_count: int = 0
    product_truth_version: Optional[int] = None
