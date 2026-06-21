"""Integration tests for the reverse proxy via the FastAPI TestClient.

The upstream provider is mocked with respx so we exercise the full
request -> record -> response path without a real LLM.
"""
from __future__ import annotations

import importlib
import json

import pytest
import respx
from httpx import Response

from app.models import Generation


@pytest.fixture()
def proxy_app(fresh_db, configured_upstream):
    """Build the app with a configured upstream, against the fresh DB.
    ``configured_upstream`` already sets the env vars and reloads all modules."""
    from app import main as main_module
    return main_module.app


@pytest.fixture()
def proxy_client(proxy_app):
    from fastapi.testclient import TestClient

    return TestClient(proxy_app)


def test_non_stream_chat_completion_is_recorded(proxy_client, fresh_db):
    upstream_payload = {
        "id": "chatcmpl-1",
        "model": "gpt-4o",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "Hello!"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
    }
    with respx.mock(base_url="https://upstream.example.com") as mock:
        mock.post("/v1/chat/completions").mock(return_value=Response(200, json=upstream_payload))

        resp = proxy_client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer client-key"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == "Hello!"

    # A generation row was written with the provider's authoritative usage.
    with fresh_db.session_scope() as db:
        gens = db.query(Generation).all()
        assert len(gens) == 1
        g = gens[0]
        assert g.model == "gpt-4o"
        assert g.status.value == "success"
        assert g.input_tokens == 8
        assert g.output_tokens == 2
        assert g.latency_ms is not None and g.latency_ms >= 0


def test_authorization_header_rewritten(proxy_client):
    """The client's key must NOT leak upstream; the real key is substituted."""
    seen_headers = {}

    def _capture(request):
        seen_headers.update(request.headers)
        return Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    with respx.mock(base_url="https://upstream.example.com") as mock:
        mock.post("/v1/chat/completions").mock(side_effect=_capture)
        proxy_client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer LEAK-THIS-PLEASE"},
        )

    assert seen_headers.get("authorization") == "Bearer secret-key"
    assert "LEAK-THIS-PLEASE" not in json.dumps(dict(seen_headers))


def test_upstream_error_is_recorded_and_passed_through(proxy_client, fresh_db):
    with respx.mock(base_url="https://upstream.example.com") as mock:
        mock.post("/v1/chat/completions").mock(
            return_value=Response(500, json={"error": {"message": "boom"}})
        )
        resp = proxy_client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert resp.status_code == 500
    with fresh_db.session_scope() as db:
        g = db.query(Generation).one()
        assert g.status.value == "error"
        assert "500" in (g.error_msg or "")


def test_upstream_network_error_records_and_returns_502(proxy_client, fresh_db):
    import httpx

    with respx.mock(base_url="https://upstream.example.com") as mock:
        mock.post("/v1/chat/completions").mock(side_effect=httpx.ConnectError("no route"))
        resp = proxy_client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
        )

    assert resp.status_code == 502
    with fresh_db.session_scope() as db:
        g = db.query(Generation).one()
        assert g.status.value == "error"


def test_streaming_chat_completion_records_accumulated_text(proxy_client, fresh_db):
    """SSE streaming: chunks are forwarded live and usage/text captured after."""
    sse = (
        'data: {"model":"gpt-4o","choices":[{"delta":{"role":"assistant","content":"Hel"}}]}\n\n'
        'data: {"model":"gpt-4o","choices":[{"delta":{"content":"lo!"}}]}\n\n'
        'data: {"model":"gpt-4o","choices":[],"usage":{"prompt_tokens":3,"completion_tokens":2}}\n\n'
        "data: [DONE]\n\n"
    )
    with respx.mock(base_url="https://upstream.example.com") as mock:
        mock.post("/v1/chat/completions").mock(
            return_value=Response(200, content=sse, headers={"content-type": "text/event-stream"})
        )
        resp = proxy_client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        )

    # The client sees the raw SSE stream re-emitted.
    text = resp.text
    assert "Hel" in text and "lo!" in text and "[DONE]" in text

    with fresh_db.session_scope() as db:
        g = db.query(Generation).one()
        assert g.status.value == "success"
        # Usage came from the streamed usage event.
        assert g.input_tokens == 3
        assert g.output_tokens == 2
        # Accumulated completion text was reconstructed for the record.
        assert g.completion_json["choices"][0]["message"]["content"] == "Hello!"
        # Time-to-first-token captured during streaming.
        assert g.ttft_ms is not None


def test_missing_upstream_config_returns_503(client):
    """Without UPSTREAM_BASE_URL, the proxy returns a clear 503."""
    resp = client.post("/v1/chat/completions", json={"model": "gpt-4o", "messages": []})
    assert resp.status_code == 503
    assert "UPSTREAM_BASE_URL" in resp.json()["error"]["message"]



def test_model_routes_to_matching_upstream(proxy_client, fresh_db, monkeypatch):
    from app import config as config_module

    monkeypatch.setenv(
        "UPSTREAMS_JSON",
        json.dumps(
            [
                {
                    "name": "openai",
                    "base_url": "https://openai.example.com/v1",
                    "api_key": "openai-key",
                    "models": ["gpt-*"],
                },
                {
                    "name": "deepseek",
                    "base_url": "https://deepseek.example.com/v1",
                    "api_key": "deepseek-key",
                    "models": ["deepseek-*"],
                },
            ]
        ),
    )
    importlib.reload(config_module)
    from app import proxy as proxy_module
    importlib.reload(proxy_module)

    seen_headers = {}

    def _capture(request):
        seen_headers.update(request.headers)
        return Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://deepseek.example.com/v1/chat/completions").mock(side_effect=_capture)
        resp = proxy_client.post(
            "/v1/chat/completions",
            json={"model": "deepseek-chat", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": "Bearer client-key"},
        )

    assert resp.status_code == 200
    assert seen_headers.get("authorization") == "Bearer deepseek-key"
    with fresh_db.session_scope() as db:
        g = db.query(Generation).one()
        assert g.provider == "deepseek"
        assert g.base_url == "https://deepseek.example.com/v1"


def test_models_endpoint_merges_all_configured_upstreams(proxy_client, monkeypatch):
    from app import config as config_module

    monkeypatch.setenv(
        "UPSTREAMS_JSON",
        json.dumps(
            [
                {
                    "name": "openai",
                    "base_url": "https://openai.example.com/v1",
                    "api_key": "openai-key",
                    "models": ["gpt-*"],
                },
                {
                    "name": "qwen",
                    "base_url": "https://qwen.example.com/v1",
                    "api_key": "qwen-key",
                    "models": ["qwen-*"],
                },
            ]
        ),
    )
    importlib.reload(config_module)
    from app import proxy as proxy_module
    importlib.reload(proxy_module)

    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://openai.example.com/v1/models").mock(
            return_value=Response(200, json={"object": "list", "data": [{"id": "gpt-4o", "object": "model"}]})
        )
        mock.get("https://qwen.example.com/v1/models").mock(
            return_value=Response(200, json={"object": "list", "data": [{"id": "qwen-plus", "object": "model"}]})
        )
        resp = proxy_client.get("/v1/models")

    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()["data"]}
    assert ids == {"gpt-4o", "qwen-plus"}
