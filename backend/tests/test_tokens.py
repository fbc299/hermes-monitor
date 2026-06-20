"""Tests for token usage estimation."""
from app.tokens import estimate_usage


def test_provider_usage_is_trusted():
    request_body = {"messages": [{"role": "user", "content": "hi"}]}
    response_body = {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    in_tok, out_tok, reported = estimate_usage(request_body, response_body)
    assert (in_tok, out_tok, reported) == (10, 5, True)


def test_estimate_when_usage_missing():
    request_body = {
        "messages": [
            {"role": "user", "content": "Hello there, how are you doing today?"},
        ]
    }
    response_body = {
        "choices": [{"message": {"role": "assistant", "content": "I'm doing well!"}}]
    }
    in_tok, out_tok, reported = estimate_usage(request_body, response_body)
    assert reported is False
    assert in_tok > 0
    assert out_tok > 0


def test_handles_multipart_content():
    request_body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            }
        ]
    }
    in_tok, out_tok, reported = estimate_usage(request_body, None)
    assert reported is False
    assert in_tok > 0  # the text part contributes tokens


def test_prompt_field_string():
    request_body = {"prompt": "Once upon a time"}
    response_body = {"choices": [{"text": "there was a token"}]}
    in_tok, out_tok, _ = estimate_usage(request_body, response_body)
    assert in_tok > 0 and out_tok > 0


def test_empty_inputs():
    assert estimate_usage(None, None) == (0, 0, False)
