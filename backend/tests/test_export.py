"""Tests for exporting generation data."""
from __future__ import annotations

import csv
import io
import json

from app.models import GenSource, GenStatus
from app.recording import record_generation


def _record_generation(db_module):
    with db_module.session_scope() as db:
        return record_generation(
            db,
            source=GenSource.PROXY,
            model="gpt-4o",
            status=GenStatus.SUCCESS,
            input_tokens=7,
            output_tokens=3,
            latency_ms=321,
            provider="openai",
            base_url="https://openai.example.com/v1",
            prompt_json={"messages": [{"role": "user", "content": "hello"}]},
            completion_json={"choices": [{"message": {"content": "hi"}}]},
        )


def test_export_traces_as_csv(client, fresh_db):
    _record_generation(fresh_db)

    resp = client.get("/api/v1/export/traces.csv")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    assert rows[0]["model"] == "gpt-4o"
    assert rows[0]["provider"] == "openai"
    assert rows[0]["total_tokens"] == "10"


def test_export_traces_as_json(client, fresh_db):
    _record_generation(fresh_db)

    resp = client.get("/api/v1/export/traces.json")

    assert resp.status_code == 200
    data = json.loads(resp.text)["data"]
    assert data[0]["model"] == "gpt-4o"
    assert data[0]["prompt"]["messages"][0]["content"] == "hello"


def test_overview_page_links_to_exports_and_auto_refresh(client):
    resp = client.get("/")

    assert resp.status_code == 200
    assert "/api/v1/export/traces.csv" in resp.text
    assert "/api/v1/export/traces.json" in resp.text
    assert "自动刷新" in resp.text
    assert "setInterval" in resp.text
