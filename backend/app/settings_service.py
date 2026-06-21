"""Runtime configuration service.

Reads config values from the database (set via the web settings UI),
falling back to environment variables. This lets users configure the
app visually without editing files or restarting containers.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from .db import session_scope
from .models import AppConfig

log = logging.getLogger(__name__)

# Keys that are user-configurable through the settings UI.
# (DB_PATH and PORT remain env-only to avoid chicken-and-egg issues.)
_CONFIG_KEYS = [
    "UPSTREAMS_JSON",
    "UPSTREAM_BASE_URL",
    "UPSTREAM_API_KEY",
    "UPSTREAM_TIMEOUT",
    "ACCESS_TOKEN",
    "PAYLOAD_STORAGE_MODE",
]


def get_config(key: str) -> str:
    """Return the effective value for *key*.

    Database value wins; environment variable is the fallback.
    """
    import os

    with session_scope() as db:
        row = db.get(AppConfig, key)
        if row is not None and row.value:
            return row.value
    return os.environ.get(key, "")


def get_all_config() -> dict[str, str]:
    """Return all known config keys as {key: effective_value}."""
    import os

    result: dict[str, str] = {}
    with session_scope() as db:
        # Fetch all known config keys in a single query.
        stmt = select(AppConfig).where(AppConfig.key.in_(_CONFIG_KEYS))
        db_rows = {row.key: row.value for row in db.execute(stmt).scalars().all()}
        for key in _CONFIG_KEYS:
            if key in db_rows:
                result[key] = db_rows[key]
            else:
                result[key] = os.environ.get(key, "")
    return result


def set_config(key: str, value: str) -> None:
    """Persist a single config value to the database."""
    with session_scope() as db:
        row = db.get(AppConfig, key)
        if row is None:
            row = AppConfig(key=key, value=value)
            db.add(row)
        else:
            row.value = value
        db.flush()
    log.info("Config '%s' updated via settings UI.", key)


def set_all_config(data: dict[str, Any]) -> None:
    """Persist multiple config values at once (e.g. from the settings form)."""
    with session_scope() as db:
        for key in _CONFIG_KEYS:
            if key in data:
                val = str(data.get(key, "")).strip()
                row = db.get(AppConfig, key)
                if row is None:
                    row = AppConfig(key=key, value=val)
                    db.add(row)
                else:
                    row.value = val
        db.flush()
    log.info("Config batch-updated via settings UI.")
