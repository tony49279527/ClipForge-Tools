from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException


MAINTENANCE_ERROR = "maintenance_mode"
MAINTENANCE_MESSAGE = "ClipForge is temporarily read-only for database maintenance."

_MAINTENANCE_ENV_NAMES = ("V3_MAINTENANCE_MODE", "CLIPFORGE_MAINTENANCE_MODE", "MAINTENANCE_MODE")
_WRITE_FREEZE_ENV_NAMES = ("V3_WRITE_FREEZE", "CLIPFORGE_WRITE_FREEZE", "WRITE_FREEZE")
_TRUE_VALUES = {"1", "true", "yes", "on"}


class MaintenanceModeError(RuntimeError):
    """Raised when a write path is blocked by maintenance mode or write freeze."""


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, "true" if default else "false").strip().lower() in _TRUE_VALUES


def is_maintenance_mode() -> bool:
    return any(_env_bool(name) for name in _MAINTENANCE_ENV_NAMES)


def is_write_freeze() -> bool:
    return any(_env_bool(name) for name in _WRITE_FREEZE_ENV_NAMES)


def writes_enabled() -> bool:
    if is_maintenance_mode() or is_write_freeze():
        return False
    if os.getenv("V3_WRITES_ENABLED"):
        return _env_bool("V3_WRITES_ENABLED", default=True)
    return True


def maintenance_payload() -> dict[str, Any]:
    return {"error": MAINTENANCE_ERROR, "message": MAINTENANCE_MESSAGE}


def assert_writes_allowed() -> None:
    if not writes_enabled():
        raise MaintenanceModeError(MAINTENANCE_MESSAGE)


def raise_maintenance_http() -> None:
    raise HTTPException(status_code=503, detail=maintenance_payload())
