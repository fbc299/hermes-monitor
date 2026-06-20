"""Token -> cost calculation.

Prices are USD per 1M tokens (input / output), matching how most providers
quote today. The table is deliberately conservative: when a model is
unknown, cost is reported as 0 rather than guessing, so the dashboard never
shows a misleading number.

Users can drop a ``prices.json`` next to the DB (path overridable via
PRICES_PATH) to add or override entries without editing code. The file
format mirrors :data:`DEFAULT_PRICES`.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Unit: USD per 1,000,000 tokens. Keys are matched as substrings of the
# lowercased model name (first match wins), so "gpt-4o" covers
# "gpt-4o-2024-08-06" etc. Update these as providers change pricing.
DEFAULT_PRICES: dict[str, tuple[float, float]] = {
    # --- OpenAI -------------------------------------------------------
    "o1-preview": (15.0, 60.0),
    "o1-mini": (3.0, 12.0),
    "o3-mini": (1.1, 4.4),
    "gpt-4o": (2.5, 10.0),
    "gpt-4-turbo": (10.0, 30.0),
    "gpt-4": (30.0, 60.0),
    "gpt-3.5": (0.5, 1.5),
    # --- Anthropic ----------------------------------------------------
    "claude-3-5-sonnet": (3.0, 15.0),
    "claude-3-5-haiku": (0.8, 4.0),
    "claude-3-opus": (15.0, 75.0),
    "claude-3-sonnet": (3.0, 15.0),
    "claude-3-haiku": (0.25, 1.25),
    # --- DeepSeek -----------------------------------------------------
    "deepseek-chat": (0.14, 0.28),
    "deepseek-reasoner": (0.55, 2.19),
    # --- StepFun / 阶跃星辰 (via OpenRouter) --------------------------
    "step-3.7-flash": (0.20, 1.15),
    "step-3.5-flash": (0.14, 1.05),
    "step-1-flash": (0.0, 0.0),   # free tier / unknown pricing
    "step-router": (0.0, 0.0),     # routing model, no direct cost
    # --- Qwen / 通义千问 (DashScope compatible pricing) ---------------
    "qwen-max": (2.88, 8.64),
    "qwen-plus": (0.4, 1.2),
    "qwen-turbo": (0.05, 0.2),
    # --- Nous Hermes / Llama-class (self-hosted: $0) ------------------
    "hermes": (0.0, 0.0),
    "llama": (0.0, 0.0),
    "qwen2": (0.0, 0.0),
    # --- Misc local runtimes -----------------------------------------
    "ollama": (0.0, 0.0),
    # --- OpenRouter markup is model-specific; default to $0 unknown --
}

_prices_cache: dict[str, tuple[float, float]] | None = None
_prices_mtime: float = 0.0
_prices_lock = threading.Lock()


def _prices_path() -> Path | None:
    """Resolve an optional user-provided ``prices.json``."""
    raw = os.environ.get("PRICES_PATH", "")
    if raw:
        return Path(raw)
    # Default: sit next to the SQLite DB.
    from .config import settings

    db = Path(settings.db_path)
    return db.parent / "prices.json"


def _load_prices() -> dict[str, tuple[float, float]]:
    """Return the effective price table, merging defaults + user overrides.

    Hot-reloads the user file when its mtime changes.
    """
    global _prices_cache, _prices_mtime

    with _prices_lock:
        path = _prices_path()
        mtime = path.stat().st_mtime if path and path.exists() else 0.0

        if _prices_cache is not None and mtime == _prices_mtime:
            return _prices_cache

        merged = dict(DEFAULT_PRICES)
        if path and path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if isinstance(v, (list, tuple)) and len(v) == 2:
                        merged[str(k)] = (float(v[0]), float(v[1]))
                log.info("Loaded %d price overrides from %s", len(data), path)
            except (json.JSONDecodeError, ValueError, OSError) as exc:
                log.warning("Ignoring invalid prices.json (%s): %s", path, exc)

        _prices_cache = merged
        _prices_mtime = mtime
        return merged


@dataclass(frozen=True)
class CostResult:
    """Outcome of a cost calculation."""

    cost: float          # USD
    priced: bool         # whether we actually knew the model's price
    price_key: str       # the matched substring key (or "" if unpriced)


def price_for(model: str) -> tuple[float, float, str] | None:
    """Look up (input_per_1m, output_per_1m, matched_key) for a model, or None."""
    if not model:
        return None
    name = model.lower()
    for key, prices in _load_prices().items():
        if key in name:
            return prices[0], prices[1], key
    return None


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> CostResult:
    """Compute the USD cost for a generation.

    Returns cost=0 and priced=False for unknown models so the caller can
    distinguish "free" (e.g. self-hosted) from "unpriced".
    """
    result = price_for(model)
    if result is None:
        return CostResult(cost=0.0, priced=False, price_key="")

    in_p, out_p, key = result
    cost = (input_tokens / 1_000_000.0) * in_p + (output_tokens / 1_000_000.0) * out_p
    return CostResult(cost=round(cost, 6), priced=True, price_key=key)
