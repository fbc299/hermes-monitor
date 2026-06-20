"""HTTP client, batching flusher, and the observe() context manager.

Design goals:
  * **Zero interference**: a reporting failure must never raise into the
    caller's code. All public methods swallow exceptions and log them.
  * **Low overhead**: events are queued and flushed by a single daemon
    thread on a timer (and at exit), so the hot path is just an enqueue.
  * **No hard dependencies**: uses the stdlib ``urllib`` if ``requests``
    isn't installed, so the SDK can be dropped into any environment.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger("hermes_monitor.sdk")

# Auto-configure from environment so users can set vars once and just call.
_DEFAULT_BASE = os.environ.get("HERMES_MONITOR_URL", "").rstrip("/")
_DEFAULT_TOKEN = os.environ.get("HERMES_MONITOR_TOKEN", "")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _post_json(url: str, payload: Any, token: str, timeout: float) -> None:
    """POST JSON with optional bearer token.

    Prefers ``requests`` if available (connection pooling), else falls back
    to stdlib urllib. Exceptions are caught by the caller, never raised up.
    """
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode("utf-8")
    try:
        try:
            import requests  # type: ignore

            requests.post(url, data=data, headers=headers, timeout=timeout)
            return
        except ImportError:
            pass
        import urllib.request

        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        urllib.request.urlopen(req, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - reporting must not raise
        log.warning("Hermes Monitor flush failed: %s", exc)


class Span:
    """A timing/collecting context for a single observed LLM call."""

    def __init__(
        self,
        monitor: "HermesMonitor",
        *,
        model: Optional[str] = None,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._monitor = monitor
        self.model = model
        self.trace_id = trace_id or uuid.uuid4().hex
        self.session_id = session_id
        self.user_id = user_id
        self.name = name
        self.metadata = dict(metadata or {})
        self._start: Optional[str] = None
        self._t0: Optional[float] = None
        self._ttft_ms: Optional[int] = None
        self._first_output = True
        self._output: Any = None
        self._input: Any = None
        self._usage: Optional[Dict[str, Any]] = None
        self._error: Optional[str] = None

    def __enter__(self) -> "Span":
        self._start = _utcnow_iso()
        self._t0 = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, _tb) -> bool:
        if exc is not None:
            self._error = f"{exc_type.__name__ if exc_type else 'Error'}: {exc}"
        self.finish()
        return False  # never swallow the caller's exception

    def set_input(self, value: Any) -> None:
        """Record the prompt (messages list or raw dict)."""
        self._input = value

    def set_output(self, value: Any) -> None:
        """Record the completion. Also captures time-to-first-output."""
        if self._first_output and self._t0 is not None:
            self._first_output = False
            self._ttft_ms = int((time.monotonic() - self._t0) * 1000)
        self._output = value

    def mark_first_token(self) -> None:
        """Explicitly record TTFT when streaming the first chunk manually."""
        if self._first_output and self._t0 is not None:
            self._first_output = False
            self._ttft_ms = int((time.monotonic() - self._t0) * 1000)

    def set_usage(self, usage: Any) -> None:
        """Record token usage (OpenAI usage object/dict, or {input,output})."""
        if usage is None:
            return
        if hasattr(usage, "model_dump"):  # pydantic / OpenAI v1 object
            try:
                usage = usage.model_dump()
            except Exception:  # noqa: BLE001
                pass
        if isinstance(usage, dict):
            self._usage = {
                "input": usage.get("input")
                or usage.get("prompt_tokens")
                or usage.get("promptTokens"),
                "output": usage.get("output")
                or usage.get("completion_tokens")
                or usage.get("completionTokens"),
            }

    def finish(self) -> None:
        """Finalize and enqueue the event. Called automatically on exit."""
        if self._t0 is None:
            self._t0 = time.monotonic()  # defensive
        latency_ms = int((time.monotonic() - self._t0) * 1000)
        meta = dict(self.metadata)
        meta["latency_ms"] = latency_ms
        if self._ttft_ms is not None:
            meta["ttft_ms"] = self._ttft_ms

        event = {
            "id": uuid.uuid4().hex,
            "type": "generation-create",
            "timestamp": _utcnow_iso(),
            "body": {
                "id": uuid.uuid4().hex,
                "model": self.model,
                "name": self.name,
                "traceId": self.trace_id,
                "sessionId": self.session_id,
                "userId": self.user_id,
                "input": self._input,
                "output": self._output,
                "startTime": self._start,
                "endTime": _utcnow_iso(),
                "metadata": meta,
                **({"usage": self._usage} if self._usage else {}),
                **({"level": "ERROR", "statusMessage": self._error} if self._error else {}),
            },
        }
        self._monitor._enqueue(event)


class HermesMonitor:
    """Main SDK entry point: configures where to report and how to flush."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        *,
        token: Optional[str] = None,
        flush_interval: float = 2.0,
        flush_at_exit: bool = True,
        timeout: float = 5.0,
    ) -> None:
        self.base_url = (base_url or _DEFAULT_BASE).rstrip("/")
        if not self.base_url:
            log.warning(
                "HermesMonitor has no base_url; events will be dropped. "
                "Set HERMES_MONITOR_URL or pass base_url=..."
            )
        self.token = token if token is not None else _DEFAULT_TOKEN
        self.flush_interval = flush_interval
        self.timeout = timeout

        self._lock = threading.Lock()
        self._queue: List[Dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        if self.base_url and flush_interval > 0:
            self._thread = threading.Thread(
                target=self._flush_loop, name="hermes-monitor-flush", daemon=True
            )
            self._thread.start()
        if flush_at_exit:
            atexit.register(self.flush)

    @property
    def _ingestion_url(self) -> str:
        return f"{self.base_url}/api/public/ingestion"

    def observe(self, **kwargs: Any) -> Span:
        """Open a timed span for an LLM call (context manager)."""
        return Span(self, **kwargs)

    def record(
        self,
        *,
        model: Optional[str] = None,
        input: Any = None,
        output: Any = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        latency_ms: Optional[int] = None,
        session_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        user_id: Optional[str] = None,
        name: Optional[str] = None,
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Report a completed call in one shot."""
        start = _utcnow_iso()
        usage: Optional[Dict[str, Any]] = None
        if input_tokens is not None or output_tokens is not None:
            usage = {"input": input_tokens or 0, "output": output_tokens or 0}
        meta = dict(metadata or {})
        if latency_ms is not None:
            meta["latency_ms"] = latency_ms
        event = {
            "id": uuid.uuid4().hex,
            "type": "generation-create",
            "timestamp": start,
            "body": {
                "id": uuid.uuid4().hex,
                "model": model,
                "name": name,
                "traceId": trace_id,
                "sessionId": session_id,
                "userId": user_id,
                "input": input,
                "output": output,
                "startTime": start,
                "endTime": _utcnow_iso(),
                "metadata": meta,
                **({"usage": usage} if usage else {}),
                **({"level": "ERROR", "statusMessage": error} if error else {}),
            },
        }
        self._enqueue(event)

    def _enqueue(self, event: Dict[str, Any]) -> None:
        if not self.base_url:
            return
        with self._lock:
            self._queue.append(event)

    def flush(self) -> None:
        """Send all queued events immediately."""
        with self._lock:
            if not self._queue:
                return
            batch = self._queue
            self._queue = []
        _post_json(
            self._ingestion_url, {"batch": batch}, self.token, self.timeout
        )

    def _flush_loop(self) -> None:
        while not self._stop.wait(self.flush_interval):
            try:
                self.flush()
            except Exception:  # noqa: BLE001
                log.debug("flush tick failed", exc_info=True)

    def shutdown(self) -> None:
        """Stop the background flusher and flush once more."""
        self._stop.set()
        try:
            self.flush()
        except Exception:  # noqa: BLE001
            pass
