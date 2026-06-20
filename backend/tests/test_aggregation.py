"""Tests for the aggregation/stats query layer."""
from datetime import datetime, timedelta, timezone

from app.models import GenSource, GenStatus
from app.recording import record_generation
from app.aggregation import by_model, daily_trend, get_generation, overview, recent_calls


def _record(db_module, **kwargs):
    with db_module.session_scope() as db:
        return record_generation(db, **kwargs)


def test_overview_counts(fresh_db):
    _record(
        fresh_db,
        source=GenSource.PROXY,
        model="gpt-4o",
        status=GenStatus.SUCCESS,
        input_tokens=100,
        output_tokens=50,
        latency_ms=800,
    )
    _record(
        fresh_db,
        source=GenSource.PROXY,
        model="gpt-4o",
        status=GenStatus.ERROR,
        input_tokens=10,
        output_tokens=0,
        latency_ms=200,
        error_msg="boom",
    )

    ov = overview()
    assert ov["total_calls"] == 2
    assert ov["input_tokens"] == 110
    assert ov["output_tokens"] == 50
    assert ov["total_tokens"] == 160
    assert ov["error_count"] == 1
    assert ov["error_rate"] == 50.0
    # gpt-4o is priced: 110 * 2.5/1M + 50 * 10/1M
    assert abs(ov["total_cost"] - (110 * 2.5 / 1_000_000 + 50 * 10 / 1_000_000)) < 0.0001


def test_by_model_groups(fresh_db):
    _record(fresh_db, source=GenSource.PROXY, model="gpt-4o", status=GenStatus.SUCCESS,
            input_tokens=100, output_tokens=100, latency_ms=500)
    _record(fresh_db, source=GenSource.PROXY, model="claude-3-5-sonnet", status=GenStatus.SUCCESS,
            input_tokens=100, output_tokens=100, latency_ms=500)

    rows = by_model()
    models = {r["model"] for r in rows}
    assert models == {"gpt-4o", "claude-3-5-sonnet"}


def test_recent_calls_ordering_and_filters(fresh_db):
    _record(fresh_db, source=GenSource.PROXY, model="gpt-4o", status=GenStatus.SUCCESS,
            input_tokens=1, output_tokens=1, prompt_json={"messages": [{"role": "user", "content": "hello world"}]})
    _record(fresh_db, source=GenSource.PROXY, model="gpt-4o", status=GenStatus.ERROR,
            input_tokens=1, output_tokens=1, prompt_json={"messages": [{"role": "user", "content": "another"}]})

    all_calls = recent_calls(limit=10)
    assert len(all_calls) == 2

    errors = recent_calls(limit=10, status="error")
    assert len(errors) == 1
    assert errors[0]["status"] == "error"

    found = recent_calls(limit=10, q="hello world")
    assert len(found) == 1
    assert found[0]["prompt"]["messages"][0]["content"] == "hello world"


def test_get_generation_returns_bodies(fresh_db):
    _record(
        fresh_db,
        source=GenSource.PROXY,
        model="gpt-4o",
        status=GenStatus.SUCCESS,
        input_tokens=1,
        output_tokens=1,
        prompt_json={"messages": [{"role": "user", "content": "hi"}]},
        completion_json={"choices": [{"message": {"content": "hello"}}]},
    )
    calls = recent_calls(limit=1)
    detail = get_generation(calls[0]["id"])
    assert detail is not None
    assert detail["prompt"]["messages"][0]["content"] == "hi"
    assert detail["completion"]["choices"][0]["message"]["content"] == "hello"


def test_daily_trend_fills_gaps(fresh_db):
    trend = daily_trend(days=7)
    assert len(trend) == 7
    assert all("day" in d and "calls" in d and "cost" in d for d in trend)
    # No data recorded yet -> all zeros.
    assert sum(d["calls"] for d in trend) == 0
