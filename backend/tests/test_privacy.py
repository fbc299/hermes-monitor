"""Tests for privacy controls around payload storage."""
from __future__ import annotations


def test_settings_page_exposes_payload_storage_mode(client):
    resp = client.get("/settings")

    assert resp.status_code == 200
    assert "保存 Prompt/Completion" in resp.text
    assert "PAYLOAD_STORAGE_MODE" in resp.text
    assert "只保存指标" in resp.text


def test_metrics_only_mode_does_not_store_prompt_or_completion(client):
    save_resp = client.post(
        "/api/v1/settings",
        json={
            "PAYLOAD_STORAGE_MODE": "metrics_only",
            "UPSTREAM_TIMEOUT": "120",
            "ACCESS_TOKEN": "",
        },
    )
    assert save_resp.status_code == 200

    from app.config import settings
    from app.db import session_scope
    from app.models import GenSource, GenStatus
    from app.recording import record_generation

    with session_scope() as db:
        gen = record_generation(
            db,
            source=GenSource.PROXY,
            model="gpt-4o",
            status=GenStatus.SUCCESS,
            input_tokens=1,
            output_tokens=1,
            prompt_json={"messages": [{"content": "secret prompt"}]},
            completion_json={"choices": [{"message": {"content": "secret answer"}}]},
            max_payload_bytes=settings.max_payload_bytes,
        )
        assert gen.prompt_json == {"_redacted": True, "reason": "metrics_only"}
        assert gen.completion_json == {"_redacted": True, "reason": "metrics_only"}
