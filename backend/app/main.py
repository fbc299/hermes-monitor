"""FastAPI application entry point.

Wires together the proxy, ingestion, query API, and dashboard pages, and
creates the database schema on startup.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI

from .api import settings as settings_api
from .api import stats as stats_api
from .api import traces as traces_api
from .api import providers as providers_api
from .api import errors as errors_api
from .api import export as export_api
from .config import settings
from .db import init_db
from .ingestion import router as ingestion_router
from .pages import router as pages_router
from .proxy import router as proxy_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("hermes_monitor")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(
        title="Hermes Monitor",
        description="Lightweight Langfuse-style LLM observability for fnOS.",
        version="0.1.0",
        # The dashboard is the root UI; the OpenAPI docs live at /docs.
        docs_url="/docs",
        redoc_url=None,
    )

    @app.on_event("startup")
    def _startup() -> None:
        init_db()
        log.info("Hermes Monitor ready on %s:%s", settings.host, settings.port)
        if settings.upstream_configured:
            log.info("Proxying to upstream: %s", settings.upstream_base_url)
        else:
            log.warning("UPSTREAM_BASE_URL not set; /v1/* will return 503.")

    # Order matters: mount the dashboard first so "/" is ours, then the API,
    # then ingestion, and finally the catch-all proxy LAST (its
    # /{path:path} route would otherwise shadow everything below it).
    app.include_router(pages_router)
    app.include_router(settings_api.router)
    app.include_router(providers_api.router)
    app.include_router(errors_api.router)
    app.include_router(export_api.router)
    app.include_router(traces_api.router)
    app.include_router(stats_api.router)
    app.include_router(ingestion_router)
    app.include_router(proxy_router)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
    )
