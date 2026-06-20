"""Token estimation as a fallback when the provider omits ``usage``.

Some upstreams (notably Ollama and certain OpenAI-compatible servers) do not
always return a ``usage`` block, especially for streaming responses. We
estimate token counts locally with a best-effort heuristic so the dashboard
never shows zeros for a call that obviously cost tokens.

Strategy, in order of preference:
  1. If the provider's ``usage`` is present, trust it.
  2. Else try ``tiktoken`` if installed (cl100k_base is a good default for
     OpenAI-class and many open models).
  3. Else fall back to a simple character/whitespace heuristic
     (~4 chars/token, matching the long-standing OpenAI rule of thumb).
"""
from __future__ import annotations

import json
from typing import Any

try:  # tiktoken is optional; import lazily and remember if it failed.
    import tiktoken  # type: ignore

    _ENCODER = tiktoken.get_encoding("cl100k_base")
except Exception:  # pragma: no cover - environment dependent
    tiktoken = None  # type: ignore
    _ENCODER = None


def _estimate_text_tokens(text: str) -> int:
    """Best-effort token count for a single string."""
    if not text:
        return 0
    if _ENCODER is not None:
        try:
            return len(_ENCODER.encode(text))
        except Exception:  # pragma: no cover
            pass
    # Heuristic: ~4 characters per token.
    return max(1, len(text) // 4)


def _extract_message_texts(messages: Any) -> list[str]:
    """Flatten an OpenAI-style messages list into plain text chunks.

    Handles string content, content-part arrays, and tool calls.
    """
    texts: list[str] = []
    if not isinstance(messages, list):
        return texts
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            texts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    if isinstance(part.get("text"), str):
                        texts.append(part["text"])
                elif isinstance(part, str):
                    texts.append(part)
        # Tool/function call payloads also consume tokens.
        for key in ("tool_calls", "function_call"):
            calls = msg.get(key)
            if isinstance(calls, list):
                texts.append(json.dumps(calls, ensure_ascii=False))
            elif isinstance(calls, dict):
                texts.append(json.dumps(calls, ensure_ascii=False))
    return texts


def estimate_usage(
    request_body: dict | None,
    response_body: dict | None,
) -> tuple[int, int, bool]:
    """Return (input_tokens, output_tokens, provider_reported).

    ``provider_reported`` is True when the numbers came from the upstream's
    ``usage`` field (authoritative), False when we estimated.
    """
    input_tokens = 0
    output_tokens = 0

    # 1) Authoritative usage from the provider response.
    if response_body and isinstance(response_body.get("usage"), dict):
        usage = response_body["usage"]
        input_tokens = int(usage.get("prompt_tokens", 0) or 0)
        output_tokens = int(usage.get("completion_tokens", 0) or 0)
        if input_tokens or output_tokens:
            return input_tokens, output_tokens, True

    # 2) Estimate input from the request messages/prompt.
    if request_body:
        if isinstance(request_body.get("messages"), list):
            for t in _extract_message_texts(request_body["messages"]):
                input_tokens += _estimate_text_tokens(t)
        elif isinstance(request_body.get("prompt"), str):
            input_tokens += _estimate_text_tokens(request_body["prompt"])

    # 3) Estimate output from the response choices.
    if response_body and isinstance(response_body.get("choices"), list):
        for choice in response_body["choices"]:
            if not isinstance(choice, dict):
                continue
            msg = choice.get("message") or {}
            if isinstance(msg, dict):
                output_tokens += _estimate_text_tokens(
                    msg.get("content") or ""
                )
                if msg.get("reasoning_content"):
                    output_tokens += _estimate_text_tokens(msg["reasoning_content"])
            text = choice.get("text")
            if isinstance(text, str):
                output_tokens += _estimate_text_tokens(text)

    return input_tokens, output_tokens, False
