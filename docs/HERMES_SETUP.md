# 接入 Hermes Agent 指南

本文档说明如何让 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 的 LLM 调用经过 Hermes Monitor 进行记录。

## 原理

Hermes Monitor 作为一个 **OpenAI 兼容反向代理**工作：

```
Hermes Agent ──→ http://NAS:8480/v1/chat/completions ──→ 真正的 LLM Provider
                 (监控记录 prompt/completion/token/延迟)        (OpenRouter/Ollama/...)
```

Hermes 只需要把自己的 `base_url` 指向监控服务，所有请求被透明转发，Hermes 完全无感知。

## 步骤

### 1. 安装 Hermes Monitor

在飞牛 NAS 上安装 Hermes Monitor（通过 .fpk 应用包或 Docker Compose），确保：
- 容器已启动且状态为 running
- 环境变量 `UPSTREAM_BASE_URL` 和 `UPSTREAM_API_KEY` 已配置

### 2. 修改 Hermes 配置

编辑 `~/.hermes/config.yaml`：

```yaml
# 修改前（直连 OpenRouter）
model:
  base_url: https://openrouter.ai/api/v1
  api_key: sk-or-v1-your-key-here
  model_name: anthropic/claude-3.5-sonnet

# 修改后（经过监控代理）
model:
  base_url: http://192.168.1.10:8480/v1    # ← 你的 NAS IP + 端口
  api_key: any-placeholder                   # ← 任意值，代理会替换
  model_name: anthropic/claude-3.5-sonnet
```

**要点**：
- `base_url` 改为 `http://<NAS_IP>:8480/v1`（注意末尾的 `/v1`）
- `api_key` 可以是任意非空值（代理会自动替换为你配置的真实 key）
- `model_name` 保持不变（透传给上游）

### 3. 验证

1. 启动 Hermes Agent，执行一次 LLM 调用
2. 打开 `http://<NAS_IP>:8480`
3. 在概览页应该能看到一条新的调用记录

## 常见 LLM 提供商的 UPSTREAM_BASE_URL

| 提供商 | UPSTREAM_BASE_URL |
|--------|-------------------|
| OpenRouter | `https://openrouter.ai/api/v1` |
| Anthropic (直连) | `https://api.anthropic.com/v1` |
| OpenAI | `https://api.openai.com/v1` |
| Groq | `https://api.groq.com/openai/v1` |
| 本地 Ollama | `http://localhost:11434/v1` |
| 本地 vLLM | `http://localhost:8000/v1` |
| LiteLLM Proxy | `http://localhost:4000/v1` |

> **注意**：如果 LLM 提供商和 Hermes Monitor 在同一台 NAS 上，需要确保端口不冲突。Hermes Monitor 默认用 8480，Ollama 用 11434，vLLM 用 8000。

## 通过 LiteLLM 统一多个提供商

如果你用 LiteLLM 作为统一网关：

```yaml
# ~/.hermes/config.yaml
model:
  base_url: http://192.168.1.10:8480/v1    # 指向监控
  api_key: any
```

Hermes Monitor 的环境变量：
```
UPSTREAM_BASE_URL=http://192.168.1.10:4000/v1    # LiteLLM 地址
UPSTREAM_API_KEY=sk-any                           # LiteLLM 的 master key
```

这样监控记录的是 Hermes→LiteLLM→最终模型的完整链路。

## 多轮会话追踪

代理层只能看到单次 LLM 请求。如果你想让多轮对话关联到同一个 trace/session，可以使用 SDK：

```python
from hermes_monitor import HermesMonitor

hm = HermesMonitor(base_url="http://192.168.1.10:8480")

# 在 Hermes 调用前包装（需要 Hermes 插件或 hook 支持）
with hm.observe(model="claude-3.5-sonnet", session_id="hermes-abc123") as span:
    span.set_input(messages)
    resp = call_llm(messages)
    span.set_output(resp)
    span.set_usage(resp.usage)
```

SDK 事件会与代理记录通过 `session_id` 关联。

## 故障排查

### Hermes 报 "upstream error"

- 检查 Hermes Monitor 容器是否运行：`docker ps | grep hermes-monitor`
- 检查环境变量是否配置正确
- 查看 Hermes Monitor 日志：`docker logs hermes-monitor`

### 看板没有数据

- 确认 Hermes 的 `base_url` 确实指向了 `http://NAS:8480/v1`（注意 `/v1` 后缀）
- 确认调用了 Hermes Agent 至少一次
- 直接测试代理是否工作：`curl http://NAS:8480/v1/models`

### 流式输出中断

- 检查网络连通性（NAS → 上游 provider）
- 某些 provider 在 SSE 流末尾不带 `[DONE]`，监控代理会自动适配
