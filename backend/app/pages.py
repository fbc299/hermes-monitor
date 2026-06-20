"""Server-rendered dashboard pages (minimal MVP, no frontend framework).

Renders small Jinja2 templates: an overview page with number cards + a
recent-calls table, a per-call detail page, and a stats page with a
per-model table and a pure-HTML daily bar trend.
"""
from __future__ import annotations

import html
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .aggregation import by_model, daily_trend, get_generation, overview, recent_calls
from .auth import require_write
from .settings_service import get_all_config
from .config import settings

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory="templates")


def _short(s: Any, n: int = 120) -> str:
    """Truncate a value to a short preview, HTML-escaped."""
    text = "" if s is None else str(s)
    if len(text) > n:
        text = text[:n] + "…"
    return html.escape(text)


def _extract_prompt_preview(gen: dict[str, Any]) -> str:
    """Pull a one-line preview of the user's prompt."""
    prompt = gen.get("prompt") or {}
    if isinstance(prompt, dict):
        messages = prompt.get("messages")
        if isinstance(messages, list):
            for msg in messages:
                if isinstance(msg, dict) and msg.get("role") == "user":
                    return _short(msg.get("content"), 120)
            # fallback: first message content
            for msg in messages:
                if isinstance(msg, dict):
                    return _short(msg.get("content"), 120)
        if isinstance(prompt.get("prompt"), str):
            return _short(prompt["prompt"], 120)
    return _short(prompt, 120)


def _extract_completion_preview(gen: dict[str, Any]) -> str:
    """Pull a one-line preview of the assistant's completion."""
    completion = gen.get("completion") or {}
    if isinstance(completion, dict):
        choices = completion.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                msg = first.get("message") or {}
                if isinstance(msg, dict):
                    return _short(msg.get("content"), 120)
                return _short(first.get("text"), 120)
    return _short(completion, 120)


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    model: str | None = Query(None),
    status: str | None = Query(None),
    q: str | None = Query(None),
) -> HTMLResponse:
    """Overview: number cards + recent calls table."""
    require_write(request)
    token = request.query_params.get("token", "")
    calls = recent_calls(limit=50, model=model, status=status, q=q)
    for c in calls:
        c["prompt_preview"] = _extract_prompt_preview(c)
        c["completion_preview"] = _extract_completion_preview(c)
    return templates.TemplateResponse(
            request,
            "overview.html",
            {
                "overview": overview(),
                "calls": calls,
                "filters": {"model": model or "", "status": status or "", "q": q or ""},
                "token": token,
                "upstream": settings.upstream_base_url or "(not configured)",
            },
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )


@router.get("/call/{gen_id}", response_class=HTMLResponse)
async def call_detail(request: Request, gen_id: str) -> HTMLResponse:
    """Full detail of one generation: prompt/completion JSON, tokens, timing."""
    require_write(request)
    gen = get_generation(gen_id)
    if gen is None:
        raise HTTPException(status_code=404, detail="generation not found")
    token = request.query_params.get("token", "")
    return templates.TemplateResponse(
        request,
        "detail.html",
        {"gen": gen, "token": token, "prompt_pretty": _pretty(gen.get("prompt")),
         "completion_pretty": _pretty(gen.get("completion"))},
    )


@router.get("/stats", response_class=HTMLResponse)
async def stats_page(request: Request) -> HTMLResponse:
    """Per-model table + daily trend bars."""
    require_write(request)
    token = request.query_params.get("token", "")
    trend = daily_trend(days=14)
    max_calls = max((d["calls"] for d in trend), default=1) or 1
    return templates.TemplateResponse(
        request,
        "stats.html",
        {
            "models": by_model(),
            "trend": trend,
            "max_calls": max_calls,
            "token": token,
        },
    )



@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    """Visual configuration page — no config file editing needed."""
    require_write(request)
    token = request.query_params.get("token", "")
    config = get_all_config()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"config": config, "token": token},
    )


def _pretty(value: Any) -> str:
    """Pretty-print JSON-ish values for the detail page."""
    import json

    if value is None:
        return "(none)"
    try:
        return html.escape(json.dumps(value, ensure_ascii=False, indent=2))
    except (TypeError, ValueError):
        return html.escape(str(value))
