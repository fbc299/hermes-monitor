# hermes-monitor SDK

Optional client for the [Hermes Monitor](../) dashboard. Use it when you
want richer context than the transparent reverse proxy captures on its own
— e.g. grouping a multi-turn Hermes conversation under a single trace, or
attaching your own `session_id` / `user_id`.

> **For most setups you don't need this at all.** Point Hermes at the proxy
> (see [docs/HERMES_SETUP.md](../docs/HERMES_SETUP.md)) and every call is
> recorded automatically. This SDK is the "extra precision" option.

## Install

```bash
pip install ./sdk          # from this repo
# or after publishing:
pip install hermes-monitor
```

No hard dependencies. If `requests` is installed it's used for connection
pooling; otherwise the stdlib `urllib` is used.

## Quick start

```python
from hermes_monitor import HermesMonitor

hm = HermesMonitor(base_url="http://192.168.1.10:8480")   # or set HERMES_MONITOR_URL

# Auto-timed context:
with hm.observe(model="gpt-4o", session_id="hermes-42") as span:
    span.set_input(messages)
    resp = client.chat.completions.create(model="gpt-4o", messages=messages)
    span.set_output(resp.choices[0].message)
    span.set_usage(resp.usage)

# One-shot:
hm.record(model="gpt-4o", input=messages, output=resp_dict,
          input_tokens=resp.usage.prompt_tokens,
          output_tokens=resp.usage.completion_tokens,
          session_id="hermes-42")
```

Events are batched and flushed on a 2-second timer (and at process exit).
Reporting failures are logged, never raised into your code.

## Environment variables

| Var | Purpose |
|-----|---------|
| `HERMES_MONITOR_URL` | Default `base_url` (e.g. `http://nas:8480`) |
| `HERMES_MONITOR_TOKEN` | Default access token (if you set `ACCESS_TOKEN` on the server) |
