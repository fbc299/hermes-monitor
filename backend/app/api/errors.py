"""Error diagnostics endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from ..aggregation import error_summary
from ..auth import require_write

router = APIRouter(prefix="/api/v1")


@router.get("/errors")
async def errors_summary(request: Request, limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
    """Grouped recent failures for the error center."""
    require_write(request)
    return {"data": error_summary(limit=limit)}
