"""Lightweight access control for management/dashboard endpoints.

Personal-use app: a single optional bearer token. When ``ACCESS_TOKEN`` is
unset, everything is open (convenient for a trusted home LAN). When set,
the dashboard and ingestion/management APIs require the token via either
the ``Authorization: Bearer ...`` header or a ``?token=`` query param.

The proxy (``/v1/*``) is intentionally NOT gated by this token: the LLM
client uses its own provider key, and we rewrite it to the upstream's.
"""
from __future__ import annotations

from fastapi import HTTPException, Request, status

from .config import settings


def _extract_token(request: Request) -> str:
    """Pull the presented token from header or query, '' if absent."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    if request.headers.get("x-access-token"):
        return request.headers["x-access-token"].strip()
    token = request.query_params.get("token")
    return (token or "").strip()


def require_write(request: Request) -> None:
    """Raise 401 if auth is enabled and the token doesn't match.

    Read endpoints share the same check — there's no read/write split for
    a single-user system.
    """
    if not settings.auth_enabled:
        return
    presented = _extract_token(request)
    if not presented or presented != settings.access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing access token.",
        )
