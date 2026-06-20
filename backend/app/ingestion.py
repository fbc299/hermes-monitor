"""SDK ingestion endpoint.

Accepts Langfuse-flavored batch events from the optional Python SDK, which
the SDK uses to attach richer context (session/user ids, multi-turn trace
grouping, explicit metrics) that the transparent proxy can't infer.

The wire format is intentionally simple and a subset of Langfuse's
``/api/public/ingestion`` so a user could even point a Langfuse SDK at us
for basic generation events.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .auth import require_write
from .db import session_scope
from .models import GenSource, GenStatus
from .recording import record_generation

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/public")


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _iso_to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        # Accept a trailing Z.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _apply_generation_event(event: dict, db) -> None:
    """Translate one Langfuse-style 'generation-create' event into a row."""
    body = event.get("body", {}) or {}
    meta = body.get("metadata", {}) or {}

    status = GenStatus.ERROR if body.get("level") == "ERROR" else GenStatus.SUCCESS
    error_msg = body.get("statusMessage") if status == GenStatus.ERROR else None

    usage = body.get("usage") or {}
    # Langfuse uses { input, output } or { promptTokens, completionTokens }.
    input_tokens = _coerce_int(
        usage.get("input") or usage.get("promptTokens")
    )
    output_tokens = _coerce_int(
        usage.get("output") or usage.get("completionTokens")
    )

    # Latency: prefer an explicit ms hint the SDK may put in metadata,
    # otherwise fall back to a start/end pair on the event body.
    latency_ms: int | None = _coerce_int(meta.get("latency_ms")) or None
    if latency_ms is None:
        start_dt = _iso_to_dt(body.get("startTime"))
        end_dt = _iso_to_dt(body.get("endTime"))
        if start_dt and end_dt:
            latency_ms = max(0, int((end_dt - start_dt).total_seconds() * 1000))

    record_generation(
        db,
        source=GenSource.SDK,
        model=body.get("model"),
        status=status,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        ttft_ms=_coerce_int(meta.get("ttft_ms")) or None,
        prompt_json=body.get("input"),
        completion_json=body.get("output"),
        provider=meta.get("provider"),
        base_url=meta.get("base_url"),
        error_msg=error_msg,
        trace_id=body.get("traceId") or event.get("traceId"),
        session_id=body.get("sessionId") or event.get("sessionId"),
        user_id=body.get("userId") or event.get("userId"),
        trace_name=body.get("name"),
        metadata_json=meta,
        created_at=_iso_to_dt(body.get("startTime"))
        or _iso_to_dt(event.get("timestamp")),
    )


@router.post("/ingestion")
async def ingestion(request: Request) -> JSONResponse:
    """Accept a batch of SDK events."""
    require_write(request)

    payload = await request.json()
    # Langfuse wraps events in { batch: [ { id, type, body, ... } ] }.
    events: list[dict] = []
    if isinstance(payload, dict):
        raw = payload.get("batch") or payload.get("events") or []
        if isinstance(raw, list):
            events = [e for e in raw if isinstance(e, dict)]
    elif isinstance(payload, list):
        events = [e for e in payload if isinstance(e, dict)]

    accepted = 0
    with session_scope() as db:
        for event in events:
            etype = (event.get("type") or "").lower()
            # We only care about generation-create events; traces/scores are no-ops
            # in this MVP but we ack them so the SDK doesn't retry.
            if "generation" in etype:
                try:
                    _apply_generation_event(event, db)
                except Exception:  # pragma: no cover
                    log.exception("Failed to ingest event %s", event.get("id"))
            accepted += 1

    return JSONResponse({"status": "ok", "accepted": accepted})
