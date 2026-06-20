"""Settings API endpoints for the web configuration UI."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from ..auth import require_write
from ..settings_service import get_all_config, set_all_config

router = APIRouter(prefix="/api/v1")


@router.get("/settings")
async def api_get_settings(request: Request) -> dict[str, Any]:
    """Return all current config values (DB overrides + env fallbacks)."""
    require_write(request)
    return {"data": get_all_config()}


@router.post("/settings")
async def api_save_settings(request: Request) -> dict[str, Any]:
    """Save config values from the settings form."""
    require_write(request)
    body = await request.json()
    set_all_config(body)
    return {"status": "ok", "data": get_all_config()}
