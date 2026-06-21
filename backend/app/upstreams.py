"""Multiple upstream provider configuration and model routing."""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class UpstreamProvider:
    """One OpenAI-compatible upstream provider."""

    name: str
    base_url: str
    api_key: str = ""
    models: tuple[str, ...] = field(default_factory=tuple)

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    @property
    def provider_label(self) -> str:
        return self.name or provider_from_url(self.base_url) or "upstream"


def provider_from_url(url: str | None) -> str | None:
    """Best-effort provider label from an upstream URL host."""
    if not url:
        return None
    try:
        host = url.split("://", 1)[-1].split("/", 1)[0]
        return host
    except Exception:
        return url


def parse_upstreams(raw_json: str, *, legacy_base_url: str = "", legacy_api_key: str = "") -> list[UpstreamProvider]:
    """Parse configured upstream providers, falling back to legacy env vars."""
    providers = _parse_json_upstreams(raw_json)
    if providers:
        return providers
    if legacy_base_url:
        return [
            UpstreamProvider(
                name=provider_from_url(legacy_base_url) or "default",
                base_url=legacy_base_url.rstrip("/"),
                api_key=legacy_api_key,
                models=("*",),
            )
        ]
    return []


def resolve_upstream(providers: list[UpstreamProvider], model: str | None) -> UpstreamProvider | None:
    """Choose the upstream for a requested model."""
    if not providers:
        return None
    if model:
        for provider in providers:
            if _matches_model(provider.models, model):
                return provider
    for provider in providers:
        if not provider.models or "*" in provider.models:
            return provider
    return providers[0]


def _parse_json_upstreams(raw_json: str) -> list[UpstreamProvider]:
    if not raw_json.strip():
        return []
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, dict):
        items = parsed.get("providers") or parsed.get("upstreams") or []
    else:
        items = parsed
    if not isinstance(items, list):
        return []
    providers: list[UpstreamProvider] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        base_url = str(item.get("base_url") or item.get("url") or "").strip().rstrip("/")
        if not base_url:
            continue
        name = str(item.get("name") or provider_from_url(base_url) or f"upstream-{idx + 1}").strip()
        api_key = str(item.get("api_key") or item.get("key") or "").strip()
        models = _normalize_models(item.get("models"))
        providers.append(
            UpstreamProvider(
                name=name,
                base_url=base_url,
                api_key=api_key,
                models=models,
            )
        )
    return providers


def _normalize_models(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
    elif isinstance(value, list):
        parts = [str(part).strip() for part in value]
    else:
        return ()
    return tuple(part for part in parts if part)


def _matches_model(patterns: tuple[str, ...], model: str) -> bool:
    if not patterns:
        return False
    return any(fnmatch.fnmatchcase(model, pattern) for pattern in patterns)
