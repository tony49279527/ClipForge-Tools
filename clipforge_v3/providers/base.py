from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from pydantic import BaseModel, ValidationError


@dataclass
class PromptCompilation:
    mode: str
    prompt_text: str
    role_map: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    validation_result: dict[str, Any] = field(default_factory=dict)


class ProviderError(RuntimeError):
    pass


T = TypeVar("T", bound=BaseModel)


def validate_structured_json(
    *,
    raw_text: str,
    model_cls: type[T],
    repair_fn: Callable[[str], str] | None = None,
) -> T:
    errors: list[str] = []
    attempts = [raw_text]
    if repair_fn is not None:
        attempts.append(repair_fn(raw_text))
    for attempt in attempts:
        try:
            payload = json.loads(attempt)
            return model_cls.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            errors.append(str(exc))
    raise ProviderError(
        f"Structured output validation failed for {model_cls.__name__}: {' | '.join(errors)}"
    )


def repair_common_json_issues(raw_text: str) -> str:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    text = text.replace("\n", " ").strip()
    if text and text[0] != "{":
        brace = text.find("{")
        if brace >= 0:
            text = text[brace:]
    if text and text[-1] != "}":
        brace = text.rfind("}")
        if brace >= 0:
            text = text[: brace + 1]
    return text
