"""Data export endpoints."""
from __future__ import annotations

import csv
import io
import json
from typing import Any

from fastapi import APIRouter, Query, Request, Response

from ..aggregation import recent_calls
from ..auth import require_write

router = APIRouter(prefix="/api/v1")

_EXPORT_COLUMNS = [
    "id",
    "created_at",
    "provider",
    "base_url",
    "model",
    "status",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cost",
    "latency_ms",
    "ttft_ms",
    "error_msg",
    "trace_id",
]


def _flat_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key, "") for key in _EXPORT_COLUMNS}


@router.get("/export/traces.csv")
async def export_traces_csv(request: Request, limit: int = Query(500, ge=1, le=5000)) -> Response:
    """Export recent calls as CSV for spreadsheets/accounting."""
    require_write(request)
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=_EXPORT_COLUMNS)
    writer.writeheader()
    for row in recent_calls(limit=limit):
        writer.writerow(_flat_row(row))
    return Response(
        content=output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="hermes-traces.csv"'},
    )


@router.get("/export/traces.json")
async def export_traces_json(request: Request, limit: int = Query(500, ge=1, le=5000)) -> Response:
    """Export recent calls as JSON with prompt/completion bodies."""
    require_write(request)
    body = {"data": recent_calls(limit=limit)}
    return Response(
        content=json.dumps(body, ensure_ascii=False),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="hermes-traces.json"'},
    )
