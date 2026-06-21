"""Tests for the visual settings page and settings API."""
from __future__ import annotations


def test_settings_page_is_visual_provider_form(client):
    resp = client.get("/settings")

    assert resp.status_code == 200
    text = resp.text
    assert "可视化设置" in text
    assert "添加上游" in text
    assert "不用手写配置" in text
    assert "高级：查看自动生成的 JSON" in text
    assert "JSON 数组" not in text


def test_settings_api_saves_visual_upstream_payload(client):
    payload = {
        "UPSTREAMS_JSON": '[{"name":"openrouter","base_url":"https://openrouter.ai/api/v1","api_key":"sk-test","models":["*"]}]',
        "UPSTREAM_BASE_URL": "https://openrouter.ai/api/v1",
        "UPSTREAM_API_KEY": "sk-test",
        "UPSTREAM_TIMEOUT": "90",
        "ACCESS_TOKEN": "",
    }

    save_resp = client.post("/api/v1/settings", json=payload)
    assert save_resp.status_code == 200

    get_resp = client.get("/api/v1/settings")
    assert get_resp.status_code == 200
    data = get_resp.json()["data"]
    assert data["UPSTREAMS_JSON"] == payload["UPSTREAMS_JSON"]
    assert data["UPSTREAM_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert data["UPSTREAM_TIMEOUT"] == "90"
