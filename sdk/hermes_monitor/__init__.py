"""Hermes Monitor SDK.

A tiny optional client that reports LLM calls to a self-hosted Hermes
Monitor instance. Use it when you want richer context than the transparent
reverse proxy can infer — e.g. to group multi-turn Hermes conversations
under one trace, or attach a user/session id from your own code.

Two ways to use it:

  1. Context manager (auto-timed):
     .. code-block:: python

         from hermes_monitor import HermesMonitor

         hm = HermesMonitor(base_url="http://nas:8480")
         with hm.observe(model="gpt-4o", session_id="abc") as span:
             resp = client.chat.completions.create(...)
             span.set_output(resp)
             span.set_usage(resp.usage)

  2. Manual one-shot:
     .. code-block:: python

         hm.record(model="gpt-4o", input=messages, output=resp_dict,
                   input_tokens=10, output_tokens=5, session_id="abc")

The SDK batches events and flushes them on a background thread; it never
raises into your application code — reporting failures are logged only.
"""
from .client import HermesMonitor, Span
from .version import __version__

__all__ = ["HermesMonitor", "Span", "__version__"]
