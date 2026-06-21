"""OpenAI-compatible reverse proxy with transparent recording.

This is the core of the app: Hermes (or any OpenAI-compatible client) points
its ``base_url`` at us, we forward the request to the real provider, capture
prompt/completion/tokens/latency on the way back, and persist a generation.

Design notes
------------
* Streaming (SSE): we must NOT buffer the whole response (memory matters on
  the N2840). We stream chunks to the client immediately and accumulate just
  the token-relevant pieces (any ``usage`` event OpenAI servers append, and
  the text of streamed deltas) so we can compute usage after the stream ends.
* Headers: the ``Authorization`` / ``api-key`` headers are *rewritten* to the
  upstream's real key, so the client never needs to know it.
* Robustness: any failure in the *recording* path is logged but never breaks
  the user's LLM call — observability must not become a reliability problem.
* Connection reuse: a module-level shared ``httpx.AsyncClient`` pools
  connections to the upstream, avoiding per-request TCP/TLS handshake costs.
"""
from __future__ import annotations

import json
import logging
import time
import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import StreamingResponse

from .config import settings
from .db import session_scope
from .models import GenSource, GenStatus
from .recording import record_generation
from .tokens import estimate_usage
from .upstreams import UpstreamProvider, resolve_upstream

log = logging.getLogger(__name__)

router = APIRouter()

# Hop-by-hop headers we must not blindly forward in either direction.
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


def _build_upstream_url(path: str, upstream: UpstreamProvider) -> str:
    """Build the full upstream URL, stripping duplicate ``v1/`` prefix.

    When the upstream base URL already ends with ``/v1`` (common for
    OpenAI-compatible providers) and the request path starts with ``v1/``,
    the ``v1/`` is stripped from the path to avoid double-prefixing.
    This handles both standard providers (``https://api.openai.com/v1``)
    and non-standard ones (``https://api.stepfun.com/step_plan/v1``).

    Strips ALL leading ``v1/`` segments (not just one), so even
    ``v1/v1/chat/completions`` is normalised correctly.
    """
    base = upstream.base_url
    if not base:
        return path
    stripped = path.lstrip("/")
    # If the base URL already ends with /v1, strip all leading v1/ from path.
    if base.endswith("/v1") or base.endswith("/v1/"):
        while stripped.startswith("v1/"):
            stripped = stripped[3:]  # remove one "v1/"
    return f"{base.rstrip('/')}/{stripped}"

# ---------------------------------------------------------------------------
# Shared httpx client — reuse connections to the upstream LLM provider.
# ---------------------------------------------------------------------------
_shared_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Return a module-level shared httpx.AsyncClient with connection pooling.

    Created lazily so the app can import the module without an upstream
    configured yet. The client is reused across all proxy requests.
    """
    global _shared_client
    if _shared_client is None:
        timeout = httpx.Timeout(settings.upstream_timeout, connect=10.0)
        _shared_client = httpx.AsyncClient(timeout=timeout)
        log.info("Shared httpx client created (pooled, timeout=%s)", timeout)
    return _shared_client


def _forwarded_headers(src_headers, *, auth_key: str | None) -> dict[str, str]:
    """Build the header dict for the upstream request.

    Replaces the client's auth with the real upstream key. OpenAI clients
    send ``Authorization: Bearer ...`` and/or ``api-key: ...``; we normalize
    to ``Authorization`` so any OpenAI-compatible server is happy.
    """
    out: dict[str, str] = {}
    for key, value in src_headers.items():
        if key.lower() in _HOP_BY_HOP:
            continue
        if key.lower() in ("authorization", "api-key"):
            continue
        out[key] = value
    if auth_key:
        out["Authorization"] = f"Bearer {auth_key}"
    return out


def _response_headers(src_headers) -> dict[str, str]:
    """Headers to copy onto the response we send back to the client."""
    out: dict[str, str] = {}
    for key, value in src_headers.items():
        if key.lower() in _HOP_BY_HOP:
            continue
        out[key] = value
    return out


def _parse_body(raw: bytes) -> dict | None:
    """Best-effort JSON decode of the request/response body."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _record(
    *,
    request_body: dict | None,
    response_body: dict | None,
    model: str | None,
    status: GenStatus,
    latency_ms: int,
    ttft_ms: int | None,
    provider: str | None,
    base_url: str | None,
    error_msg: str | None,
    session_id: str | None,
    trace_id: str | None,
) -> None:
    """Persist one generation. Never raises (observability is best-effort)."""
    try:
        input_tokens, output_tokens, _reported = estimate_usage(
            request_body, response_body
        )
        with session_scope() as db:
            record_generation(
                db,
                source=GenSource.PROXY,
                model=model,
                status=status,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                ttft_ms=ttft_ms,
                prompt_json=request_body,
                completion_json=response_body,
                provider=provider,
                base_url=base_url,
                error_msg=error_msg,
                trace_id=trace_id,
                session_id=session_id,
            )
    except Exception:  # pragma: no cover - must never break the call
        log.exception("Failed to record proxy generation")


async def _proxy_non_stream(
    request: Request,
    client: httpx.AsyncClient,
    path: str,
    raw_body: bytes,
    request_body: dict | None,
) -> Response:
    """Forward a non-streaming request and record the full round trip."""
    is_stream = bool(request_body and request_body.get("stream"))
    model = request_body.get("model") if request_body else None
    upstream_config = resolve_upstream(settings.upstreams, model)
    if upstream_config is None:
        return _missing_upstream_response()
    session_id = _extract_session_id(request, request_body)
    trace_id = request.headers.get("x-trace-id")

    upstream_url = _build_upstream_url(path, upstream_config)
    fwd_headers = _forwarded_headers(request.headers, auth_key=upstream_config.api_key)
    start = time.monotonic()
    error_msg: str | None = None
    status = GenStatus.SUCCESS

    try:
        upstream = await client.post(
            upstream_url,
            content=raw_body,
            headers=fwd_headers,
        )
    except httpx.RequestError as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        error_msg = f"upstream request error: {exc}"
        log.warning(error_msg)
        _record(
            request_body=request_body,
            response_body=None,
            model=model,
            status=GenStatus.ERROR,
            latency_ms=latency_ms,
            ttft_ms=None,
            provider=upstream_config.provider_label,
            base_url=upstream_config.base_url,
            error_msg=error_msg,
            session_id=session_id,
            trace_id=trace_id,
        )
        return Response(
            content=json.dumps({"error": {"message": error_msg, "type": "upstream_error"}}),
            status_code=502,
            media_type="application/json",
        )

    latency_ms = int((time.monotonic() - start) * 1000)
    response_body = _parse_body(upstream.content)
    if upstream.status_code >= 400:
        status = GenStatus.ERROR
        error_msg = f"upstream status {upstream.status_code}"

    _record(
        request_body=request_body,
        response_body=response_body,
        model=model,
        status=status,
        latency_ms=latency_ms,
        ttft_ms=None,
        provider=upstream_config.provider_label,
        base_url=upstream_config.base_url,
        error_msg=error_msg,
        session_id=session_id,
        trace_id=trace_id,
    )

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_response_headers(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )


def _extract_session_id(request: Request, body: dict | None) -> str | None:
    """Pull an optional session id from headers or body metadata."""
    sid = request.headers.get("x-session-id")
    if sid:
        return sid
    if body and isinstance(body.get("metadata"), dict):
        meta = body["metadata"]
        return meta.get("session_id") or meta.get("hermes_session_id")
    return None


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

async def _proxy_stream(
    request: Request,
    client: httpx.AsyncClient,
    path: str,
    raw_body: bytes,
    request_body: dict | None,
) -> StreamingResponse:
    """Forward a streaming request, capturing usage on the fly."""
    model = request_body.get("model") if request_body else None
    upstream_config = resolve_upstream(settings.upstreams, model)
    if upstream_config is None:
        return StreamingResponse(iter([b'{"error":"upstream_not_configured"}']), status_code=503, media_type="application/json")  # type: ignore
    session_id = _extract_session_id(request, request_body)
    trace_id = request.headers.get("x-trace-id")

    upstream_url = _build_upstream_url(path, upstream_config)
    fwd_headers = _forwarded_headers(request.headers, auth_key=upstream_config.api_key)

    start = time.monotonic()
    ttft_ms: int | None = None

    # Open a streaming request to the upstream.
    try:
        req = client.build_request(
            "POST", upstream_url, content=raw_body, headers=fwd_headers
        )
        upstream = await client.send(req, stream=True)
    except httpx.RequestError as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        error_msg = f"upstream request error: {exc}"
        log.warning(error_msg)
        _record(
            request_body=request_body,
            response_body=None,
            model=model,
            status=GenStatus.ERROR,
            latency_ms=latency_ms,
            ttft_ms=None,
            provider=upstream_config.provider_label,
            base_url=upstream_config.base_url,
            error_msg=error_msg,
            session_id=session_id,
            trace_id=trace_id,
        )
        return StreamingResponse(iter([b'{"error":"upstream_error"}']), media_type="application/json")  # type: ignore

    status_code = upstream.status_code
    resp_headers = _response_headers(upstream.headers)
    is_error = status_code >= 400
    aiter_lines = upstream.aiter_lines()

    captured_usage: dict | None = None
    captured_text: list[str] = []
    captured_model: str | None = model
    captured_role: str | None = None

    async def replay():
        nonlocal ttft_ms, captured_usage, captured_model, captured_role
        error_msg: str | None = None if not is_error else f"upstream status {status_code}"
        try:
            first = True
            async for line in aiter_lines:
                if first:
                    first = False
                    ttft_ms = int((time.monotonic() - start) * 1000)
                yield (line + "\n").encode("utf-8")
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    continue
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if captured_model is None and chunk.get("model"):
                    captured_model = chunk["model"]
                if isinstance(chunk.get("usage"), dict):
                    captured_usage = chunk["usage"]
                for choice in chunk.get("choices", []) or []:
                    if not isinstance(choice, dict):
                        continue
                    delta = choice.get("delta") or {}
                    if delta.get("role"):
                        captured_role = delta["role"]
                    content = delta.get("content")
                    if isinstance(content, str):
                        captured_text.append(content)
        finally:
            try:
                await upstream.aclose()
            except Exception:
                pass  # upstream close errors must not suppress recording
            latency_ms = int((time.monotonic() - start) * 1000)
            reconstructed = None
            if not is_error:
                body = {
                    "model": captured_model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": captured_role or "assistant",
                                "content": "".join(captured_text),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                }
                if captured_usage:
                    body["usage"] = captured_usage
                reconstructed = body
            # Recording failure must never break the stream — best-effort.
            try:
                _record(
                    request_body=request_body,
                    response_body=reconstructed,
                    model=captured_model or model,
                    status=GenStatus.ERROR if is_error else GenStatus.SUCCESS,
                    latency_ms=latency_ms,
                    ttft_ms=ttft_ms,
                    provider=upstream_config.provider_label,
                    base_url=upstream_config.base_url,
                    error_msg=error_msg,
                    session_id=session_id,
                    trace_id=trace_id,
                )
            except Exception:
                log.exception("Failed to record streaming generation")

    return StreamingResponse(
        replay(),
        status_code=status_code,
        headers=resp_headers,
        media_type=resp_headers.get("content-type", "text/event-stream"),
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/v1/chat/completions")
@router.post("/v1/completions")
async def chat_completions(request: Request) -> Response:
    """Proxy endpoint for chat/text completions."""
    return await _handle(request, request.url.path.lstrip("/"))


@router.get("/v1/models")
async def list_models(request: Request) -> Response:
    """Fetch model lists from every configured upstream in parallel."""
    if not settings.upstream_configured:
        return _missing_upstream_response()

    client = _get_client()
    providers = settings.upstreams

    async def fetch(provider: UpstreamProvider) -> tuple[UpstreamProvider, httpx.Response | Exception]:
        try:
            response = await client.get(
                _build_upstream_url("v1/models", provider),
                headers=_forwarded_headers(request.headers, auth_key=provider.api_key),
            )
            return provider, response
        except httpx.RequestError as exc:
            return provider, exc

    results = await asyncio.gather(*(fetch(provider) for provider in providers))
    models: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    seen: set[str] = set()
    for provider, result in results:
        if isinstance(result, Exception):
            errors.append({"provider": provider.provider_label, "error": str(result)})
            continue
        if result.status_code >= 400:
            errors.append({"provider": provider.provider_label, "error": f"status {result.status_code}"})
            continue
        payload = _parse_body(result.content) or {}
        items = payload.get("data", []) if isinstance(payload, dict) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "")
            dedupe_key = f"{provider.provider_label}:{model_id}"
            if not model_id or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            merged = dict(item)
            merged.setdefault("owned_by", provider.provider_label)
            models.append(merged)

    if not models and errors:
        return Response(
            content=json.dumps({"error": {"message": "all upstream model requests failed", "details": errors}}),
            status_code=502,
            media_type="application/json",
        )
    body: dict[str, Any] = {"object": "list", "data": models}
    if errors:
        body["upstream_errors"] = errors
    return Response(content=json.dumps(body), media_type="application/json")


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def passthrough(request: Request, path: str) -> Response:
    """Catch-all transparent proxy for any other upstream route.

    Non-completion routes (e.g. ``/v1/models``, ``/v1/embeddings``) are
    forwarded without recording — only chat/text completions are observed.
    """
    if path.startswith("v1/chat/completions") or path.startswith("v1/completions"):
        return await _handle(request, path)
    return await _handle(request, path, record=False)


async def _handle(request: Request, path: str, *, record: bool = True) -> Response:
    """Dispatch a request to streaming or non-streaming handling."""
    if not settings.upstream_configured:
        return _missing_upstream_response()

    raw_body = await request.body()
    body = _parse_body(raw_body)
    is_stream = bool(body and body.get("stream"))

    client = _get_client()
    if is_stream and record:
        return await _proxy_stream(request, client, path, raw_body, body)
    if record:
        return await _proxy_non_stream(request, client, path, raw_body, body)
    # Non-recorded passthrough.
    upstream_config = resolve_upstream(settings.upstreams, body.get("model") if body else None)
    if upstream_config is None:
        return _missing_upstream_response()
    upstream_url = _build_upstream_url(path, upstream_config)
    fwd_headers = _forwarded_headers(
        request.headers, auth_key=upstream_config.api_key
    )
    method = request.method
    upstream = await client.request(
        method, upstream_url, content=raw_body, headers=fwd_headers
    )
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=_response_headers(upstream.headers),
        media_type=upstream.headers.get("content-type"),
    )



def _missing_upstream_response() -> Response:
    return Response(
        content=json.dumps(
            {
                "error": {
                    "message": "No upstream is configured. Set UPSTREAMS_JSON or legacy UPSTREAM_BASE_URL.",
                    "type": "config_error",
                }
            }
        ),
        status_code=503,
        media_type="application/json",
    )
