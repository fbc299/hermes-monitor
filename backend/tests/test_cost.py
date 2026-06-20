"""Tests for token -> cost calculation."""
from app.cost import compute_cost, price_for


def test_known_model_returns_priced_cost():
    # gpt-4o: $2.5 / 1M input, $10 / 1M output
    result = compute_cost("gpt-4o-2024-08-06", input_tokens=1_000_000, output_tokens=500_000)
    assert result.priced is True
    # 1M * 2.5 + 0.5M * 10 = 2.5 + 5.0 = 7.5
    assert abs(result.cost - 7.5) < 1e-6


def test_self_hosted_model_is_free_but_priced():
    # hermes / llama are listed at 0 -> priced but cost 0.
    result = compute_cost("Hermes-3-Llama-3.1-70B", input_tokens=1000, output_tokens=2000)
    assert result.priced is True
    assert result.cost == 0.0


def test_unknown_model_is_unpriced():
    result = compute_cost("some-brand-new-model-v9", input_tokens=1000, output_tokens=1000)
    assert result.priced is False
    assert result.cost == 0.0


def test_price_for_substring_match():
    # "claude-3-5-sonnet" key should match the versioned name.
    prices = price_for("claude-3-5-sonnet-20241022")
    assert prices == (3.0, 15.0, "claude-3-5-sonnet")


def test_empty_model_returns_none():
    assert price_for("") is None
    assert price_for(None) is None  # type: ignore[arg-type]
