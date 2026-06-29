from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class V3ContinuityStateRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: int = Field(ge=1)
    shot_id: int = Field(ge=1)
    product_state_json: dict[str, Any] = Field(default_factory=dict)
    machine_state_json: dict[str, Any] = Field(default_factory=dict)
    workpiece_state_json: dict[str, Any] = Field(default_factory=dict)
    camera_state_json: dict[str, Any] = Field(default_factory=dict)
    lighting_state_json: dict[str, Any] = Field(default_factory=dict)
    environment_state_json: dict[str, Any] = Field(default_factory=dict)
    action_state_json: dict[str, Any] = Field(default_factory=dict)
    sound_state_json: dict[str, Any] = Field(default_factory=dict)
    source_take_id: Optional[int] = Field(default=None, ge=1)
    version: int = Field(default=1, ge=1)
