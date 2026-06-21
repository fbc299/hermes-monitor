"""Tests for provider diagnostics and routing visibility."""
from __future__ import annotations

import importlib
import json

import pytest
import respx
from httpx import Response


@pytest.fixture()
def multi_provider_client(fresh_db, monkeypatch):
    from app import config as config_module

    monkeypatch.setenv(
        "UPSTREAMS_JSON",
        json.dumps(
            [
                {
                    "name": "openai",
                    "base_url": "https://openai.example.com/v1",
                    "api_key": "openai-key",
                    "models": ["gpt-*", "o*"],
                },
                {
                    "name": "qwen",
                    "base_url": "https://qwen.example.com/v1",
                    "api_key": "qwen-key",
                    "models": ["qwen-*", "*"],
                },
            ]
        ),
    )
    importlib.reload(config_module)
    from app import proxy as proxy_module
    importlib.reload(proxy_module)
    from app import main as main_module
    importlib.reload(main_module)

    from fastapi.testclient import TestClient

    return TestClient(main_module.app)


def test_provider_health_reports_each_upstream(multi_provider_client):
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://openai.example.com/v1/models").mock(
            return_value=Response(200, json={"object": "list", "data": [{"id": "gpt-4o"}]})
        )
        mock.get("https://qwen.example.com/v1/models").mock(
            return_value=Response(503, json={"error": "maintenance"})
        )

        resp = multi_provider_client.get("/api/v1/providers/health")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data[0]["name"] == "openai"
    assert data[0]["ok"] is True
    assert data[0]["model_count"] == 1
    assert data[0]["latency_ms"] >= 0
    assert data[1]["name"] == "qwen"
    assert data[1]["ok"] is False
    assert data[1]["status_code"] == 503


def test_provider_models_show_routing_rules(multi_provider_client):
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://openai.example.com/v1/models").mock(
            return_value=Response(200, json={"object": "list", "data": [{"id": "gpt-4o"}]})
        )
        mock.get("https://qwen.example.com/v1/models").mock(
            return_value=Response(200, json={"object": "list", "data": [{"id": "qwen-plus"}]})
        )

        resp = multi_provider_client.get("/api/v1/providers/models")

    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert {
        (row["id"], row["provider"], tuple(row["routing_patterns"])) for row in rows
    } == {
        ("gpt-4o", "openai", ("gpt-*", "o*")),
        ("qwen-plus", "qwen", ("qwen-*", "*")),
    }


def test_settings_page_has_provider_diagnostics(multi_provider_client):
    resp = multi_provider_client.get("/settings")

    assert resp.status_code == 200
    assert "测试所有上游" in resp.text
    assert "刷新模型列表" in resp.text
    assert "模型路由预览" in resp.text
