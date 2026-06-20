"""Tests for the SDK ingestion endpoint."""
from app.models import Generation


def test_ingestion_creates_generation(client, fresh_db):
    payload = {
        "batch": [
            {
                "id": "evt-1",
                "type": "generation-create",
                "body": {
                    "id": "gen-1",
                    "model": "gpt-4o",
                    "name": "my trace",
                    "traceId": "trace-1",
                    "sessionId": "hermes-session-1",
                    "userId": "me",
                    "input": {"messages": [{"role": "user", "content": "hi"}]},
                    "output": {"choices": [{"message": {"content": "hello"}}]},
                    "usage": {"input": 5, "output": 3},
                    "startTime": "2026-06-20T10:00:00Z",
                    "endTime": "2026-06-20T10:00:01Z",
                    "metadata": {"provider": "openai", "latency_ms": 1200},
                },
            },
            {
                "id": "evt-2",
                "type": "trace-create",
                "body": {"id": "trace-1", "name": "my trace"},
            },
        ]
    }
    resp = client.post("/api/public/ingestion", json=payload)
    assert resp.status_code == 200
    assert resp.json()["accepted"] == 2

    with fresh_db.session_scope() as db:
        gens = db.query(Generation).all()
        assert len(gens) == 1
        g = gens[0]
        assert g.model == "gpt-4o"
        assert g.source.value == "sdk"
        assert g.input_tokens == 5
        assert g.output_tokens == 3
        assert g.trace_id == "trace-1"
        # latency from explicit metadata
        assert g.latency_ms == 1200


def test_ingestion_latencies_from_start_end(client, fresh_db):
    """When no explicit latency_ms, derive it from start/end timestamps."""
    payload = {
        "batch": [
            {
                "type": "generation-create",
                "body": {
                    "model": "gpt-4o",
                    "usage": {"input": 1, "output": 1},
                    "startTime": "2026-06-20T10:00:00.000Z",
                    "endTime": "2026-06-20T10:00:02.500Z",
                },
            }
        ]
    }
    client.post("/api/public/ingestion", json=payload)
    with fresh_db.session_scope() as db:
        g = db.query(Generation).one()
        assert g.latency_ms == 2500


def test_ingestion_accepts_plain_list(client, fresh_db):
    payload = [{"type": "generation-create", "body": {"model": "gpt-4o", "usage": {"input": 1, "output": 1}}}]
    resp = client.post("/api/public/ingestion", json=payload)
    assert resp.status_code == 200
    with fresh_db.session_scope() as db:
        assert db.query(Generation).count() == 1


def test_ingestion_requires_token_when_enabled(app, monkeypatch, tmp_path):
    """With ACCESS_TOKEN set, ingestion must be rejected without it."""
    db_file = str(tmp_path / "token_test.db")
    monkeypatch.setenv("ACCESS_TOKEN", "topsecret")
    monkeypatch.setenv("DB_PATH", db_file)

    from tests.conftest import _reload_config_and_dependents
    _reload_config_and_dependents()

    from app import db as db_module
    from app import main as main_module
    db_module.reset_engine_for_tests(db_file)
    db_module.init_db()

    from fastapi.testclient import TestClient

    c = TestClient(main_module.app)
    resp = c.post("/api/public/ingestion", json={"batch": []})
    assert resp.status_code == 401

    resp = c.post(
        "/api/public/ingestion?token=topsecret", json={"batch": []}
    )
    assert resp.status_code == 200
