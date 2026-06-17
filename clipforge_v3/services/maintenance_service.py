from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException


MAINTENANCE_ERROR = "maintenance_mode"
MAINTENANCE_MESSAGE = "ClipForge is temporarily read-only for database maintenance."


class MaintenanceModeError(RuntimeError):
    """Raised when a write path is blocked by maintenance mode."""


def is_maintenance_mode() -> bool:
    return os.getenv("CLIPFORGE_MAINTENANCE_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}


def maintenance_payload() -> dict[str, Any]:
    return {"error": MAINTENANCE_ERROR, "message": MAINTENANCE_MESSAGE}


def assert_writes_allowed() -> None:
    if is_maintenance_mode():
        raise MaintenanceModeError(MAINTENANCE_MESSAGE)


def raise_maintenance_http() -> None:
    raise HTTPException(status_code=503, detail=maintenance_payload())
