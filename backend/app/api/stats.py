"""Aggregate statistics endpoints for the dashboard."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from ..aggregation import by_model, daily_trend, overview
from ..auth import require_write

router = APIRouter(prefix="/api/v1")


@router.get("/stats/overview")
async def stats_overview(
    request: Request,
    period: str = Query("today", pattern="^(today|24h|7d|30d|all)$"),
) -> dict[str, Any]:
    """Headline metrics for the chosen period."""
    require_write(request)
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    since = {
        "today": now.replace(hour=0, minute=0, second=0, microsecond=0),
        "24h": now - timedelta(hours=24),
        "7d": now - timedelta(days=7),
        "30d": now - timedelta(days=30),
        "all": None,
    }[period]
    return {"data": overview(since=since)}


@router.get("/stats/models")
async def stats_models(request: Request, period: str = "7d") -> dict[str, Any]:
    """Per-model breakdown."""
    require_write(request)
    from datetime import datetime, timedelta, timezone

    since = None
    if period != "all":
        days = {"today": 1, "24h": 1, "7d": 7, "30d": 30}.get(period, 7)
        since = datetime.now(timezone.utc) - timedelta(days=days)
    return {"data": by_model(since=since)}


@router.get("/stats/daily")
async def stats_daily(request: Request, days: int = Query(14, ge=1, le=90)) -> dict[str, Any]:
    """Calls + cost per day for a simple trend view."""
    require_write(request)
    return {"data": daily_trend(days=days)}
