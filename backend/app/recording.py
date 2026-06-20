"""Recording service: persist generations, traces, and session aggregates.

Both the reverse proxy and the SDK ingestion endpoint funnel through here,
so the write path is centralized. Keeping it small and synchronous-on-commit
is fine for personal-scale volume (hundreds to low-thousands of calls/day).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import String
from sqlalchemy import select
from sqlalchemy.orm import Session

from .cost import compute_cost
from .models import Generation, GenSource, GenStatus, Trace

log = logging.getLogger(__name__)


def _new_id() -> str:
    """URL-safe unique id for a generation."""
    return uuid.uuid4().hex


def _truncate_payload(value: Any, max_bytes: int) -> Any:
    """Cap a JSON-serializable payload size to avoid unbounded row growth.

    If a prompt/completion is enormous (e.g. a file dump), we store a small
    placeholder instead of the whole blob. Token counts are still recorded.
    """
    if value is None:
        return None
    try:
        size = len(str(value).encode("utf-8", errors="ignore"))
    except Exception:
        size = 0
    if size <= max_bytes:
        return value
    if isinstance(value, (dict, list)):
        return {"_truncated": True, "_original_bytes": size}
    text = str(value)
    return {"_truncated": True, "_original_bytes": size, "_preview": text[:512]}


def upsert_trace(
    db: Session,
    trace_id: str | None,
    session_id: str | None,
    user_id: str | None,
    name: str | None,
    metadata_json: dict | None,
) -> str | None:
    """Create a trace row if it doesn't exist. Returns the trace id."""
    if not trace_id:
        # Allow an implicit auto-created trace when a session is given but
        # no trace id; this helps proxy-captured calls group together.
        return None
    existing = db.get(Trace, trace_id)
    if existing is None:
        db.add(
            Trace(
                id=trace_id,
                session_id=session_id,
                user_id=user_id,
                name=name,
                metadata_json=metadata_json,
            )
        )
    return trace_id


def record_generation(
    db: Session,
    *,
    source: GenSource,
    model: str | None,
    status: GenStatus,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: int | None = None,
    ttft_ms: int | None = None,
    prompt_json: Any = None,
    completion_json: Any = None,
    provider: str | None = None,
    base_url: str | None = None,
    error_msg: str | None = None,
    trace_id: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    trace_name: str | None = None,
    metadata_json: dict | None = None,
    max_payload_bytes: int = 256 * 1024,
    created_at: datetime | None = None,
) -> Generation:
    """Persist a single generation and update session aggregates.

    This is the single write entry point for both proxy and SDK paths.
    """
    # Bind to (or create) a trace/session if context was provided.
    resolved_trace_id = upsert_trace(
        db, trace_id, session_id, user_id, trace_name, metadata_json
    )

    total_tokens = input_tokens + output_tokens
    cost_result = compute_cost(model or "", input_tokens, output_tokens)

    gen = Generation(
        id=_new_id(),
        trace_id=resolved_trace_id,
        model=model,
        source=source,
        status=status,
        prompt_json=_truncate_payload(prompt_json, max_payload_bytes),
        completion_json=_truncate_payload(completion_json, max_payload_bytes),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        latency_ms=latency_ms,
        ttft_ms=ttft_ms,
        cost=cost_result.cost,
        provider=provider,
        base_url=base_url,
        error_msg=error_msg,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.add(gen)

    if session_id:
        _touch_session(db, session_id, total_tokens, cost_result.cost)

    db.flush()
    return gen


def _touch_session(
    db: Session, session_id: str, total_tokens: int, cost: float
) -> None:
    """Incrementally update the aggregate row for a session (single upsert)."""
    from sqlalchemy import text

    db.execute(
        text(
            "INSERT INTO sessions (id, call_count, total_tokens, total_cost, "
            "first_seen, last_seen) "
            "VALUES (:id, 1, :tokens, :cost, datetime('now'), datetime('now')) "
            "ON CONFLICT(id) DO UPDATE SET "
            "call_count = sessions.call_count + 1, "
            "total_tokens = sessions.total_tokens + :tokens, "
            "total_cost = sessions.total_cost + :cost, "
            "last_seen = datetime('now')"
        ),
        {"id": session_id, "tokens": total_tokens, "cost": cost},
    )
    db.flush()


def list_generations(
    db: Session,
    *,
    limit: int = 50,
    offset: int = 0,
    model: str | None = None,
    status: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    q: str | None = None,
) -> list[Generation]:
    """Filtered, paginated generation list for the dashboard/API."""
    stmt = select(Generation).order_by(Generation.created_at.desc())
    if model:
        stmt = stmt.where(Generation.model == model)
    if status:
        stmt = stmt.where(Generation.status == status)
    if since:
        stmt = stmt.where(Generation.created_at >= since)
    if until:
        stmt = stmt.where(Generation.created_at <= until)
    if q:
        # Substring search on the serialized prompt body. JSON column search
        # in SQLite is limited, so we cast to text. Good enough for personal use.
        like = f"%{q}%"
        stmt = stmt.where(Generation.prompt_json.cast(String).like(like))
    return db.execute(stmt.limit(limit).offset(offset)).scalars().all()
