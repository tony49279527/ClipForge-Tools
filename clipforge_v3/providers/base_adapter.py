from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class VideoGenerationProvider(ABC):
    @abstractmethod
    def validate_capabilities(self, *, mode: str, reference_roles: list[dict]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def build_payload(self, *, prompt_text: str, mode: str, ratio: str, duration: int, resolution: str, generate_audio: bool, reference_roles: list[dict]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def submit_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_task_status(self, task_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def cancel_task(self, task_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def extract_result(self, response: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def estimate_cost(self, *, duration: int, resolution: str) -> float:
        raise NotImplementedError

    @abstractmethod
    def normalize_error(self, error: Exception | dict[str, Any] | str) -> dict[str, Any]:
        raise NotImplementedError
