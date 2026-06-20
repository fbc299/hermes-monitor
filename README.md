# Hermes Monitor 🜂

> 轻量级 LLM 调用追踪与看板，对标 [Langfuse](https://langfuse.com)，为飞牛 NAS 低配硬件设计。

专为监控 **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** 的 LLM 调用而构建，同时兼容任何 OpenAI 兼容客户端。

## 特性

- **零侵入接入** — 把 Hermes 的 `base_url` 指向监控代理，所有 LLM 调用自动被记录
- **OpenAI 兼容反向代理** — 完整透传 `/v1/*` 请求，支持 SSE 流式
- **调用追踪** — 记录 prompt/completion/token/延迟/成本/错误
- **数据看板** — 今日概览、调用列表、按模型统计、每日趋势
- **可选 SDK** — Python 上下文管理器，补充多轮会话上下文
- **极轻资源** — 单进程 FastAPI + SQLite (WAL)，内存 <150MB，无额外服务

## 架构

```
Hermes Agent ──(base_url)──► Hermes Monitor ──(透传)──► 真正的 LLM Provider
                             ├─ /v1/*          (反向代理 + 记录)
                             ├─ /api/v1/*      (查询 API)
                             └─ /              (看板页面)
                                      │
                                      ▼
                                   SQLite (WAL)
```

## 快速开始

### 方式一：飞牛 NAS 应用包 (.fpk)

1. 构建 Docker 镜像（见 [构建说明](#构建)）
2. 推送到 Docker Hub 或导出 tar
3. 用 `fnpack` 打包为 `.fpk`（见 `fpk/` 目录）
4. 在飞牛「应用中心」→「手动安装」导入 `.fpk`
5. 配置环境变量（见下方）

### 方式二：Docker Compose

```yaml
services:
  hermes-monitor:
    build: ./backend
    container_name: hermes-monitor
    restart: unless-stopped
    ports:
      - "8480:8000"
    volumes:
      - ./data:/app/data
    environment:
      - DB_PATH=/app/data/monitor.db
      - UPSTREAM_BASE_URL=https://openrouter.ai/api/v1
      - UPSTREAM_API_KEY=sk-or-v1-xxxxx
      - ACCESS_TOKEN=my-secret     # 可选：看板访问口令
```

```bash
docker compose up -d
```

### 方式三：直接运行

```bash
cd backend
pip install -r requirements.txt
UPSTREAM_BASE_URL=https://openrouter.ai/api/v1 \
UPSTREAM_API_KEY=sk-xxxxx \
python -m app.main
```

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `UPSTREAM_BASE_URL` | ✅ | | 真正的 LLM 提供商地址 |
| `UPSTREAM_API_KEY` | ✅ | | 真正的 LLM API Key |
| `DB_PATH` | | `data/monitor.db` | SQLite 数据库路径 |
| `PORT` | | `8000` | 监听端口 |
| `ACCESS_TOKEN` | | | 看板/API 访问口令（留空则无需认证） |
| `UPSTREAM_TIMEOUT` | | `120` | 上游请求超时(秒) |
| `PRICES_PATH` | | DB 同目录 | 自定义价格表 JSON 路径 |

## 接入 Hermes Agent

安装好监控后，在 Hermes 的配置中改一行：

```yaml
# ~/.hermes/config.yaml
model:
  base_url: http://192.168.1.10:8480/v1   # 你的 NAS IP + 监控端口
  api_key: any-placeholder                  # 代理会替换为真实 key
```

详细的 Hermes 配置说明见 [docs/HERMES_SETUP.md](docs/HERMES_SETUP.md)。

## 看板

访问 `http://<NAS_IP>:8480`（如果设置了 `ACCESS_TOKEN`，加 `?token=xxx`）。

- **概览页** `/` — 今日调用数/成本/token/延迟/错误率 + 最近 50 条调用
- **调用详情** `/call/<id>` — 完整 prompt/completion JSON、token 明细
- **统计页** `/stats` — 按模型分组 + 每日趋势柱状图

## 成本计算

内置常见模型价格表（gpt-4o、claude-3.5、deepseek、qwen 等），自托管模型（hermes、llama、ollama）标记为 $0。

可在数据库同目录放一个 `prices.json` 自定义：

```json
{
  "gpt-4o": [2.5, 10.0],
  "my-model": [1.0, 2.0]
}
```

格式：`[美元/百万输入 token, 美元/百万输出 token]`，匹配模型名的子串。

## 可选 SDK

```bash
pip install ./sdk
```

```python
from hermes_monitor import HermesMonitor

hm = HermesMonitor(base_url="http://nas:8480")

with hm.observe(model="gpt-4o", session_id="hermes-42") as span:
    span.set_input(messages)
    resp = client.chat.completions.create(model="gpt-4o", messages=messages)
    span.set_output(resp)
    span.set_usage(resp.usage)
```

## 测试

```bash
cd backend
pip install -r requirements-dev.txt
pytest
```

## 项目结构

```
hermes-monitor/
├── backend/           # FastAPI 后端（Docker 镜像内容）
│   ├── app/           # 应用代码
│   │   ├── main.py        # 入口
│   │   ├── config.py      # 配置
│   │   ├── db.py          # SQLite (WAL)
│   │   ├── models.py      # ORM 模型
│   │   ├── proxy.py       # ★ OpenAI 兼容反向代理
│   │   ├── ingestion.py   # SDK 上报端点
│   │   ├── recording.py    # 写入服务
│   │   ├── aggregation.py  # 统计聚合
│   │   ├── cost.py        # 成本计算
│   │   ├── tokens.py      # Token 估算
│   │   ├── auth.py        # 访问控制
│   │   ├── pages.py       # 看板页面路由
│   │   └── api/           # JSON API
│   ├── templates/      # Jinja2 看板模板
│   ├── tests/         # 单元/集成测试
│   ├── Dockerfile
│   └── requirements.txt
├── sdk/               # 可选 Python SDK
├── fpk/               # 飞牛 .fpk 打包源
│   ├── manifest
│   ├── app/docker/docker-compose.yaml
│   ├── app/ui/config
│   ├── cmd/           # 生命周期脚本
│   └── config/        # privilege + resource
└── docs/
```

## 许可

MIT
