"""Shared pytest fixtures.

Each test gets a fresh in-memory SQLite database so tests are isolated and
fast. We re-point the app"s DB engine before importing/creating the app and
reload dependent modules so that ``config.settings`` propagates everywhere.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# Make the backend importable: tests/ is under backend/, app/ is a sibling.
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

# All modules that depend on config and need reloading when env vars change.
# ``app.db`` and ``app.main`` are handled separately because db owns the
# engine lifecycle and main rebuilds the FastAPI routers.
_CONFIG_DEPENDENTS = [
    "app.cost",
    "app.tokens",
    "app.auth",
    "app.recording",
    "app.aggregation",
    "app.pages",
    "app.proxy",
    "app.ingestion",
    "app.api.traces",
    "app.api.stats",
]


def _reload_config_and_dependents(
    *, skip_db: bool = False, skip_main: bool = False
):
    """Reload config then every module that imports from .config.

    Parameters
    ----------
    skip_db : bool
        Keep the current ``app.db`` module (and its engine) untouched.
        Used when other fixtures already set up the engine.
    skip_main : bool
        Don"t rebuild FastAPI. The caller will handle it separately.
    """
    from app import config as _config
    importlib.reload(_config)

    for mod_name in _CONFIG_DEPENDENTS:
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])

    if not skip_db and "app.db" in sys.modules:
        importlib.reload(sys.modules["app.db"])

    if not skip_main and "app.main" in sys.modules:
        importlib.reload(sys.modules["app.main"])


@pytest.fixture()
def fresh_db(monkeypatch, tmp_path):
    """Point the app at a temp file DB, create tables, then rebuild the app."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("UPSTREAM_BASE_URL", "")
    monkeypatch.setenv("UPSTREAM_API_KEY", "")
    monkeypatch.setenv("UPSTREAMS_JSON", "")
    monkeypatch.setenv("ACCESS_TOKEN", "")

    # 1. Reload everything so config picks up the env vars above.
    _reload_config_and_dependents()

    # 2. Build the engine and schema against the current config.
    from app import db as db_module
    db_module.reset_engine_for_tests(db_file)
    db_module.init_db()

    yield db_module

    # Cleanup: dispose engine so tmp_path can be removed.
    db_module._engine.dispose() if db_module._engine else None


@pytest.fixture()
def app(fresh_db):
    """A FastAPI instance against the fresh DB."""
    from app import main as main_module
    return main_module.app


@pytest.fixture()
def client(app):
    """A TestClient wired to the test app."""
    from fastapi.testclient import TestClient
    return TestClient(app)


@pytest.fixture()
def configured_upstream(monkeypatch):
    """Set upstream env vars and reload config+modules so proxy sees them.

    Does NOT touch ``app.db`` so the engine from ``fresh_db`` stays intact.
    """
    monkeypatch.setenv("UPSTREAM_BASE_URL", "https://upstream.example.com")
    monkeypatch.setenv("UPSTREAM_API_KEY", "secret-key")
    _reload_config_and_dependents(skip_db=True)
    from app import config as config_module
    return config_module
