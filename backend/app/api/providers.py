"""Provider diagnostics endpoints."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from fastapi import APIRouter, Request

from ..auth import require_write
from ..config import settings
from ..proxy import _build_upstream_url, _forwarded_headers, _parse_body
from ..upstreams import UpstreamProvider

router = APIRouter(prefix="/api/v1")


async def _fetch_models(
    client: httpx.AsyncClient,
    request: Request,
    provider: UpstreamProvider,
) -> dict[str, Any]:
    """Fetch /models for one provider and return a diagnostic row."""
    started = time.monotonic()
    row: dict[str, Any] = {
        "name": provider.provider_label,
        "base_url": provider.base_url,
        "routing_patterns": list(provider.models),
        "ok": False,
        "status_code": None,
        "latency_ms": 0,
        "model_count": 0,
        "models": [],
        "error": None,
    }
    try:
        response = await client.get(
            _build_upstream_url("v1/models", provider),
            headers=_forwarded_headers(request.headers, auth_key=provider.api_key),
        )
        row["status_code"] = response.status_code
        row["ok"] = response.status_code < 400
        if row["ok"]:
            payload = _parse_body(response.content) or {}
            items = payload.get("data", []) if isinstance(payload, dict) else []
            row["models"] = [item for item in items if isinstance(item, dict)]
            row["model_count"] = len(row["models"])
        else:
            row["error"] = f"status {response.status_code}"
    except httpx.RequestError as exc:
        row["error"] = str(exc)
    finally:
        row["latency_ms"] = int((time.monotonic() - started) * 1000)
    return row


async def _provider_rows(request: Request) -> list[dict[str, Any]]:
    timeout = httpx.Timeout(settings.upstream_timeout, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await asyncio.gather(
            *(_fetch_models(client, request, provider) for provider in settings.upstreams)
        )


@router.get("/providers/health")
async def providers_health(request: Request) -> dict[str, Any]:
    """Health-check every configured upstream provider."""
    require_write(request)
    return {"data": await _provider_rows(request)}


@router.get("/providers/models")
async def providers_models(request: Request) -> dict[str, Any]:
    """Return model IDs annotated with provider and routing rules."""
    require_write(request)
    rows = await _provider_rows(request)
    out: list[dict[str, Any]] = []
    for row in rows:
        for model in row["models"]:
            model_id = str(model.get("id") or "")
            if not model_id:
                continue
            out.append(
                {
                    "id": model_id,
                    "provider": row["name"],
                    "base_url": row["base_url"],
                    "routing_patterns": row["routing_patterns"],
                    "owned_by": model.get("owned_by") or row["name"],
                }
            )
    return {"data": out, "providers": rows}
