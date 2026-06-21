"""Aggregation queries powering the dashboard and stats API.

Kept as plain functions returning simple dicts/lists so both the JSON API
and the Jinja2 templates can consume them without duplication.

All ranges are computed in Python rather than SQL date-trunc, because SQLite
has no native date type and we store timezone-aware datetimes as ISO strings
(SQLAlchemy handles the round-trip). For personal-scale data this is fine.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from .db import session_scope
from .models import Generation, GenStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


def overview(since: datetime | None = None) -> dict[str, Any]:
    """Top-line numbers for the dashboard header cards."""
    with session_scope() as db:
        agg_cols = [
            func.count(Generation.id),
            func.coalesce(func.sum(Generation.input_tokens), 0),
            func.coalesce(func.sum(Generation.output_tokens), 0),
            func.coalesce(func.sum(Generation.total_tokens), 0),
            func.coalesce(func.sum(Generation.cost), 0.0),
            func.coalesce(func.avg(Generation.latency_ms), 0.0),
        ]
        agg_stmt = select(*agg_cols)
        err_stmt = select(func.count(Generation.id)).where(
            Generation.status == GenStatus.ERROR
        )
        if since is not None:
            cond = Generation.created_at >= since
            agg_stmt = agg_stmt.where(cond)
            err_stmt = err_stmt.where(cond)

        row = db.execute(agg_stmt).one()
        total, in_tok, out_tok, all_tok, cost, avg_lat = row
        errors = db.execute(err_stmt).one()[0]

    total = total or 0
    return {
        "total_calls": total,
        "input_tokens": int(in_tok or 0),
        "output_tokens": int(out_tok or 0),
        "total_tokens": int(all_tok or 0),
        "total_cost": round(float(cost or 0.0), 4),
        "avg_latency_ms": round(float(avg_lat or 0.0), 1),
        "error_count": int(errors or 0),
        "error_rate": round((errors / total) * 100, 2) if total else 0.0,
    }


def by_model(since: datetime | None = None) -> list[dict[str, Any]]:
    """Per-model breakdown: calls, tokens, cost, avg latency."""
    with session_scope() as db:
        stmt = select(
            Generation.model,
            func.count(Generation.id),
            func.coalesce(func.sum(Generation.total_tokens), 0),
            func.coalesce(func.sum(Generation.cost), 0.0),
            func.coalesce(func.avg(Generation.latency_ms), 0.0),
        ).group_by(Generation.model)
        if since:
            stmt = stmt.where(Generation.created_at >= since)
        rows = db.execute(stmt).all()

    rows = sorted(rows, key=lambda r: (r[0] or ""), reverse=False)
    return [
        {
            "model": r[0] or "(unknown)",
            "calls": int(r[1]),
            "total_tokens": int(r[2]),
            "cost": round(float(r[3]), 4),
            "avg_latency_ms": round(float(r[4]), 1),
        }
        for r in rows
    ]


def daily_trend(days: int = 14) -> list[dict[str, Any]]:
    """Calls + cost per UTC day for the last N days (for the simple bar view).

    Uses SQLite's ``unixepoch()`` and ``date()`` for server-side grouping
    (SQLite 3.38+), avoiding loading all rows into Python memory.
    """
    from sqlalchemy import text

    cutoff = _now() - timedelta(days=days)
    with session_scope() as db:
        rows = db.execute(
            text(
                "SELECT date(created_at) AS day, "
                "COUNT(*) AS calls, "
                "COALESCE(SUM(cost), 0.0) AS cost "
                "FROM generations "
                "WHERE created_at >= :cutoff "
                "GROUP BY day ORDER BY day"
            ),
            {"cutoff": cutoff.isoformat()},
        ).all()
    per_day = {r[0]: {"calls": int(r[1]), "cost": float(r[2])} for r in rows if r[0]}

    # Fill any gaps so the chart has continuous days.
    today = _now().date()
    out = []
    for i in range(days - 1, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        d = per_day.get(day, {"calls": 0, "cost": 0.0})
        out.append({"day": day, "calls": d["calls"], "cost": round(d["cost"], 4)})
    return out


def recent_calls(limit: int = 50, **filters: Any) -> list[dict[str, Any]]:
    """Recent generations as lightweight dicts for table rendering."""
    from .recording import list_generations

    with session_scope() as db:
        gens = list_generations(db, limit=limit, **filters)
        return [_generation_to_dict(g) for g in gens]


def error_summary(limit: int = 50) -> dict[str, Any]:
    """Grouped error diagnostics for the error center."""
    with session_scope() as db:
        total = db.execute(
            select(func.count(Generation.id)).where(Generation.status == GenStatus.ERROR)
        ).one()[0]
        provider_rows = db.execute(
            select(Generation.provider, func.count(Generation.id), func.max(Generation.created_at))
            .where(Generation.status == GenStatus.ERROR)
            .group_by(Generation.provider)
            .order_by(func.count(Generation.id).desc())
        ).all()
        model_rows = db.execute(
            select(Generation.model, func.count(Generation.id), func.max(Generation.created_at))
            .where(Generation.status == GenStatus.ERROR)
            .group_by(Generation.model)
            .order_by(func.count(Generation.id).desc())
        ).all()
        message_rows = db.execute(
            select(Generation.error_msg, func.count(Generation.id), func.max(Generation.created_at))
            .where(Generation.status == GenStatus.ERROR)
            .group_by(Generation.error_msg)
            .order_by(func.count(Generation.id).desc())
            .limit(10)
        ).all()
        recent_rows = db.execute(
            select(Generation)
            .where(Generation.status == GenStatus.ERROR)
            .order_by(Generation.created_at.desc())
            .limit(limit)
        ).scalars().all()
    return {
        "total_errors": int(total or 0),
        "by_provider": [
            {"provider": row[0] or "(unknown)", "errors": int(row[1] or 0), "last_seen": row[2].isoformat() if row[2] else None}
            for row in provider_rows
        ],
        "by_model": [
            {"model": row[0] or "(unknown)", "errors": int(row[1] or 0), "last_seen": row[2].isoformat() if row[2] else None}
            for row in model_rows
        ],
        "by_message": [
            {"message": row[0] or "(no message)", "errors": int(row[1] or 0), "last_seen": row[2].isoformat() if row[2] else None}
            for row in message_rows
        ],
        "recent": [_generation_to_dict(row) for row in recent_rows],
    }


def _generation_to_dict(g: Generation) -> dict[str, Any]:
    """Serialize a Generation row for the API / template."""
    return {
        "id": g.id,
        "model": g.model,
        "source": g.source.value if g.source else None,
        "status": g.status.value if g.status else None,
        "input_tokens": g.input_tokens,
        "output_tokens": g.output_tokens,
        "total_tokens": g.total_tokens,
        "latency_ms": g.latency_ms,
        "ttft_ms": g.ttft_ms,
        "cost": round(float(g.cost or 0.0), 6),
        "provider": g.provider,
        "error_msg": g.error_msg,
        "trace_id": g.trace_id,
        "session_id": None,  # joined from trace if needed; kept light here
        "created_at": g.created_at.isoformat() if g.created_at else None,
        "prompt": g.prompt_json,
        "completion": g.completion_json,
    }


def get_generation(gen_id: str) -> dict[str, Any] | None:
    """Full detail for the call-detail page, including bodies."""
    with session_scope() as db:
        g = db.get(Generation, gen_id)
        if g is None:
            return None
        return _generation_to_dict(g)
