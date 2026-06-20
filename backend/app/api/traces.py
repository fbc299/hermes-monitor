"""Generation/trace query endpoints for the dashboard's data layer."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from ..aggregation import get_generation, recent_calls
from ..auth import require_write

router = APIRouter(prefix="/api/v1")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@router.get("/traces")
async def list_traces(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    model: str | None = Query(None),
    status: str | None = Query(None),
    since: str | None = Query(None),
    until: str | None = Query(None),
    q: str | None = Query(None),
) -> dict[str, Any]:
    """List recent generations with optional filtering/pagination."""
    require_write(request)
    rows = recent_calls(
        limit=limit,
        model=model,
        status=status,
        since=_parse_dt(since),
        until=_parse_dt(until),
        q=q,
    )
    return {"data": rows[offset:], "count": len(rows), "limit": limit}


@router.get("/traces/{gen_id}")
async def get_trace(request: Request, gen_id: str) -> dict[str, Any]:
    """Full detail for a single generation (prompt/completion bodies)."""
    require_write(request)
    row = get_generation(gen_id)
    if row is None:
        raise HTTPException(status_code=404, detail="generation not found")
    return {"data": row}
