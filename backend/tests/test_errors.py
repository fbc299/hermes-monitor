"""Tests for the error center page and API."""
from __future__ import annotations

from app.models import GenSource, GenStatus
from app.recording import record_generation


def _record_error(db_module, *, model="gpt-4o", provider="openai", error="upstream status 500"):
    with db_module.session_scope() as db:
        record_generation(
            db,
            source=GenSource.PROXY,
            model=model,
            status=GenStatus.ERROR,
            input_tokens=10,
            output_tokens=0,
            latency_ms=123,
            provider=provider,
            base_url=f"https://{provider}.example.com/v1",
            error_msg=error,
            prompt_json={"messages": [{"role": "user", "content": "hello"}]},
        )


def test_errors_api_groups_recent_failures(client, fresh_db):
    _record_error(fresh_db, model="gpt-4o", provider="openai", error="upstream status 500")
    _record_error(fresh_db, model="gpt-4o", provider="openai", error="upstream status 500")
    _record_error(fresh_db, model="qwen-plus", provider="qwen", error="upstream request error: timeout")

    resp = client.get("/api/v1/errors")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total_errors"] == 3
    assert data["by_provider"][0]["provider"] == "openai"
    assert data["by_provider"][0]["errors"] == 2
    assert data["by_model"][0]["model"] == "gpt-4o"
    assert data["recent"][0]["status"] == "error"


def test_errors_page_renders_error_center(client, fresh_db):
    _record_error(fresh_db)

    resp = client.get("/errors")

    assert resp.status_code == 200
    assert "错误中心" in resp.text
    assert "按 Provider 分组" in resp.text
    assert "upstream status 500" in resp.text
