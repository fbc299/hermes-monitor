"""Application configuration, loaded from environment variables.

Server-level settings (host, port, db_path) are env-only.
Business settings (upstream, access token) can be overridden through
the web settings UI and stored in the database.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .upstreams import UpstreamProvider, parse_upstreams


def _env(key: str, default: str = "") -> str:
    """Read an environment variable, defaulting to an empty string."""
    return os.environ.get(key, default)


def _db_or_env(key: str, default: str = "") -> str:
    """Read from the database settings table, falling back to env."""
    try:
        from .settings_service import get_config

        val = get_config(key)
        if val:
            return val
    except Exception:
        pass  # DB not ready yet (e.g. during startup before init_db)
    return _env(key, default)


@dataclass(frozen=True)
class Settings:
    # --- Server ---------------------------------------------------------
    host: str = field(default_factory=lambda: _env("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(_env("PORT", "8000")))

    # --- Storage --------------------------------------------------------
    # SQLite database file path. Mounted as a volume in the fnOS container.
    db_path: str = field(default_factory=lambda: _env("DB_PATH", "data/monitor.db"))

    # --- Upstream LLM provider (the real backend we proxy to) -----------
    # Read from DB first (set via settings UI), env var as fallback.
    @property
    def upstream_base_url(self) -> str:
        return _db_or_env("UPSTREAM_BASE_URL", "").rstrip("/")

    @property
    def upstream_api_key(self) -> str:
        return _db_or_env("UPSTREAM_API_KEY", "")

    @property
    def upstreams_json(self) -> str:
        return _db_or_env("UPSTREAMS_JSON", "")

    @property
    def upstreams(self) -> list[UpstreamProvider]:
        return parse_upstreams(
            self.upstreams_json,
            legacy_base_url=self.upstream_base_url,
            legacy_api_key=self.upstream_api_key,
        )

    # --- Security -------------------------------------------------------
    # Optional simple bearer token for the dashboard / management API.
    # When set, browser/API access requires `?token=` or Authorization header.
    @property
    def access_token(self) -> str:
        return _db_or_env("ACCESS_TOKEN", "")

    # --- Tuning ---------------------------------------------------------
    # Max seconds to wait for the upstream provider (OpenAI/Ollama/etc.).
    @property
    def upstream_timeout(self) -> float:
        try:
            return float(_db_or_env("UPSTREAM_TIMEOUT", "120"))
        except ValueError:
            return 120.0

    @property
    def payload_storage_mode(self) -> str:
        mode = _db_or_env("PAYLOAD_STORAGE_MODE", "full").strip().lower()
        return mode if mode in {"full", "metrics_only"} else "full"

    # Max body size (bytes) we persist to SQLite for prompt/completion JSON.
    max_payload_bytes: int = field(
        default_factory=lambda: int(_env("MAX_PAYLOAD_BYTES", str(256 * 1024)))
    )

    @property
    def upstream_configured(self) -> bool:
        """Whether a real upstream provider has been configured."""
        return bool(self.upstreams)

    @property
    def auth_enabled(self) -> bool:
        """Whether dashboard access control is active."""
        return bool(self.access_token)


settings = Settings()
