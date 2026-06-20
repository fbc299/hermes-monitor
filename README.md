# Hermes Monitor 🜂

> 轻量级 LLM 调用追踪与监控看板 — 为 Hermes Agent 和飞牛 NAS 低配硬件设计

## 概述

Hermes Monitor 是一个零侵入的 LLM 调用监控代理，工作在 Hermes Agent 和真正的 LLM 提供商之间。所有 LLM 请求自动经过代理并被记录，提供实时调用追踪、token 统计、费用估算和可视化看板。

**核心设计原则：**
- **零侵入** — 只需修改 Hermes 的 `base_url`，无需修改业务代码
- **观察不阻塞** — 记录失败不影响 LLM 调用本身
- **极低资源** — 单进程 FastAPI + SQLite (WAL)，内存 <150MB，适合 NAS 环境
- **OpenAI 兼容** — 代理完整的 `/v1/*` 请求，支持 SSE 流式响应

## 架构

```
Hermes Agent ──(base_url)──► Hermes Monitor ──(透传)──► LLM Provider (StepFun/OpenAI/...)
                             │
                    ┌────────┼────────┐
                    ▼        ▼        ▼
                /v1/*    /api/v1/*    /
              (代理+记录) (查询API)  (看板)
                    │
                    ▼
              SQLite (WAL)
```

请求流向：
1. Hermes Agent 将 `base_url` 指向 `http://localhost:8480/v1`
2. Monitor 接收请求，重写 `Authorization` 头为真实上游 Key
3. 透明转发到上游 LLM 提供商（如 StepFun）
4. 在响应返回时捕获 prompt/completion/token/延迟/费用
5. 持久化到 SQLite 数据库

## 代码详解

### 1. `proxy.py` — 核心反向代理

这是整个系统的核心。`proxy.py` 实现了一个透明的 OpenAI 兼容反向代理，同时完成调用记录。

**关键设计：**

#### 路径去重 (`_build_upstream_url`)
```python
def _build_upstream_url(path: str) -> str:
    base = settings.upstream_base_url
    stripped = path.lstrip("/")
    if base.endswith("/v1") or base.endswith("/v1/"):
        while stripped.startswith("v1/"):
            stripped = stripped[3:]  # 去重所有 v1/ 前缀
    return f"{base.rstrip('/')}/{stripped}"
```

当上游 URL 已包含 `/v1`（如 `https://api.stepfun.com/step_plan/v1`），而客户端路径也以 `v1/` 开头时，使用 `while` 循环去除所有重复的 `v1/` 前缀，避免 URL 变成 `/step_plan/v1/v1/chat/completions` 导致 404。

#### 流式处理 (`_proxy_stream`)
流式响应（SSE）不缓存整个响应体，而是逐块转发给客户端，同时累积 delta 文本和 `usage` 事件。在流结束后重建完整的 response body 用于记录。这样在 N2840 这种低内存 NAS 上也能正常工作。

#### 连接复用 (`_shared_client`)
模块级共享的 `httpx.AsyncClient`，避免每次请求重新建立 TCP/TLS 连接，减少延迟。

#### 认证重写 (`_forwarded_headers`)
将客户端的 `Authorization` / `api-key` 头替换为真实上游 API Key，客户端无需知道真实密钥。

### 2. `recording.py` — 写入服务

集中化的写入入口，proxy 和 SDK 两条路径都通过 `record_generation()` 持久化。

- **负载截断** — `_truncate_payload()` 限制 prompt/completion 存储大小（默认 256KB），防止超大 payload 撑爆数据库
- **Session 聚合** — `_touch_session()` 使用 SQLite 的 `ON CONFLICT` upsert 语法，增量更新 session 的调用次数、token 总量和费用

### 3. `tokens.py` — Token 估算

三层策略计算 token 数：
1. **优先使用 Provider 返回的 `usage`** — 最准确
2. **回退到 tiktoken** — 如果安装了 `tiktoken`，使用 `cl100k_base` 编码器
3. **最终回退到字符估算** — 约 4 字符/token

支持从 messages 数组、tool_calls、function_call 和 `reasoning_content`（推理模型）中提取文本进行估算。

### 4. `cost.py` — 费用计算

内置主流模型价格表（USD/百万 token），包括：
- OpenAI: GPT-4o, GPT-4-turbo, GPT-3.5, o1/o3 系列
- Anthropic: Claude 3.5 Sonnet/Haiku, Claude 3 Opus/Sonnet/Haiku
- DeepSeek: deepseek-chat, deepseek-reasoner
- StepFun: step-3.7-flash, step-3.5-flash
- Qwen: qwen-max/plus/turbo
- 自托管模型（hermes, llama, ollama）标记为 $0

支持通过 `prices.json` 文件自定义价格，并自动热加载（基于 mtime 检测）。

### 5. `aggregation.py` — 统计聚合

纯 Python 函数实现的聚合查询，避免 SQLite 日期函数兼容问题：
- `overview()` — 今日概览（调用数、token、费用、延迟、错误率）
- `by_model()` — 按模型分组统计
- `daily_trend()` — 14 天每日趋势，自动填补空白日期
- `recent_calls()` — 分页调用列表

### 6. `config.py` — 配置管理

双层配置机制：
- **环境变量** — 作为初始值和回退值
- **数据库** — 通过 Web 设置页面修改后存入 `app_config` 表，优先级高于环境变量

`_db_or_env()` 函数自动处理优先级，启动时如果 DB 未就绪则回退到环境变量。

### 7. `pages.py` — 看板页面

服务端渲染的 Jinja2 模板页面：
- `/` — 概览页：数字卡片 + 最近 50 条调用 + 筛选
- `/call/<id>` — 调用详情：完整 prompt/completion JSON
- `/stats` — 统计页：按模型分组 + 14 天趋势柱状图
- `/settings` — 设置页：Web 界面修改上游配置

所有页面添加 `Cache-Control: no-cache` 头确保实时数据。

### 8. 前端模板

`overview.html` 和 `detail.html` 包含 JavaScript 时间转换脚本，将 UTC 时间戳自动转换为浏览器本地时间显示。

## 修复过程记录

以下是部署和调试过程中遇到的关键问题及修复方案。

### 修复 1: 路径重复导致 404

**问题：** 当 `UPSTREAM_BASE_URL` 设置为 `https://api.stepfun.com/step_plan/v1` 时，客户端请求路径 `/v1/chat/completions` 被直接拼接，变成 `/step_plan/v1/v1/chat/completions`，上游返回 404。

**根因：** StepFun 的 API 路径结构为 `/step_plan/v1/chat/completions`，而 OpenRouter 的路径为 `/api/v1/chat/completions`。原始代码简单拼接 `base_url + path`，没有处理 `v1/` 前缀冲突。

**修复：** 在 `proxy.py` 中添加 `_build_upstream_url()` 函数，使用 `while` 循环去除所有重复的 `v1/` 前缀：
```python
if base.endswith("/v1") or base.endswith("/v1/"):
    while stripped.startswith("v1/"):
        stripped = stripped[3:]
```
替换了 3 处 URL 构造调用（`_proxy_non_stream`、`_proxy_stream`、`_handle`）。

**验证：** 测试 `/v1/chat/completions` 和 `/v1/v1/chat/completions` 均返回 200。

### 修复 2: 数据库覆盖环境变量

**问题：** 容器用环境变量设置了 `UPSTREAM_BASE_URL=https://api.stepfun.com/step_plan/v1`，但实际请求仍发送到 `https://api.stepfun.com`（不带 `/step_plan/v1`）。

**根因：** 之前通过 Web 设置页面把 `UPSTREAM_BASE_URL` 存入了数据库 `app_config` 表，旧值为 `https://api.stepfun.com`。`config.py` 的 `_db_or_env()` 函数优先读取数据库，导致环境变量被覆盖。

**修复：** 直接更新数据库中的值：
```sql
UPDATE app_config SET value = 'https://api.stepfun.com/step_plan/v1' WHERE key = 'UPSTREAM_BASE_URL';
```

**教训：** 数据库中的配置值优先级高于环境变量。修改环境变量后需要同步更新数据库，或通过 Web 设置页面修改。

### 修复 3: 时区问题

**问题：** 监控面板显示的时间为 UTC（比北京时间晚 8 小时），且容器内时间为 UTC。

**修复分两步：**

1. **容器层** — 挂载宿主机时区文件并设置 `TZ` 环境变量：
   ```bash
   -v /etc/localtime:/etc/localtime:ro
   -e TZ=Asia/Shanghai
   ```

2. **前端层** — 在 `overview.html` 和 `detail.html` 添加 JavaScript 脚本，将 UTC 时间戳转换为浏览器本地时间：
   ```javascript
   document.querySelectorAll('.time-cell').forEach(el => {
     const utc = el.getAttribute('data-time');
     const d = new Date(utc + 'Z');
     // 格式化为本地时间
     el.textContent = local;
   });
   ```

### 修复 4: Gateway 配置统一

**问题：** Hermes Gateway 的多个 provider 配置不一致：
- 主模型 `base_url` 直连 `https://api.stepfun.com/step_plan/v1`（绕过监控）
- 辅助模型使用硬编码 IP `192.168.1.10:8480`
- 不同 provider 使用不同的 API Key

**修复：** 统一所有 provider 的 `base_url` 为 `http://localhost:8480/v1`：
```bash
hermes config set model.base_url http://localhost:8480/v1
```

修改了 4 处配置：
- `model.base_url`（默认模型）
- `custom_providers[0].base_url`（自定义 provider）
- `openai.base_url`（OpenAI 路由）
- `auxiliary.vision.base_url`（视觉模型）

### 修复 5: 容器端口绑定

**问题：** 容器使用 `-p 127.0.0.1:8480:8000`，只绑定 localhost，外部设备无法访问 `192.168.1.10:8480`。

**修复：** 改为 `-p 8480:8000`（绑定所有接口）。

### 修复 6: 前端缓存

**问题：** 浏览器缓存看板页面，数据不实时更新。

**修复：** 在 `pages.py` 的 dashboard 响应中添加 `Cache-Control: no-cache, no-store, must-revalidate` 头。

## 快速开始

### Docker 运行

```bash
# 构建镜像
cd backend
docker build -t hermes-monitor:latest .

# 运行容器
docker run -d --name hermes-monitor --restart=unless-stopped \
  -p 8480:8000 \
  -v /tmp/hermes-data:/app/data \
  -v /etc/localtime:/etc/localtime:ro \
  -e TZ=Asia/Shanghai \
  -e UPSTREAM_BASE_URL=https://api.stepfun.com/step_plan/v1 \
  -e UPSTREAM_API_KEY=your-api-key \
  hermes-monitor:latest
```

### Docker Compose

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
      - /etc/localtime:/etc/localtime:ro
    environment:
      - TZ=Asia/Shanghai
      - DB_PATH=/app/data/monitor.db
      - UPSTREAM_BASE_URL=https://api.stepfun.com/step_plan/v1
      - UPSTREAM_API_KEY=your-api-key
      - ACCESS_TOKEN=my-secret     # 可选：看板访问口令
```

### 接入 Hermes Agent

```yaml
# ~/.hermes/config.yaml
model:
  base_url: http://localhost:8480/v1
  api_key: any-placeholder   # 代理会替换为真实 key
```

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `UPSTREAM_BASE_URL` | ✅ | | 真正的 LLM 提供商地址 |
| `UPSTREAM_API_KEY` | ✅ | | 真正的 LLM API Key |
| `DB_PATH` | | `data/monitor.db` | SQLite 数据库路径 |
| `PORT` | | `8000` | 监听端口 |
| `HOST` | | `0.0.0.0` | 监听地址 |
| `ACCESS_TOKEN` | | | 看板/API 访问口令（留空则无需认证） |
| `UPSTREAM_TIMEOUT` | | `120` | 上游请求超时(秒) |
| `MAX_PAYLOAD_BYTES` | | `262144` | prompt/completion 最大存储字节 |
| `PRICES_PATH` | | DB 同目录 | 自定义价格表 JSON 路径 |
| `TZ` | | | 时区（推荐 `Asia/Shanghai`） |

## 看板

访问 `http://<NAS_IP>:8480`（如果设置了 `ACCESS_TOKEN`，加 `?token=xxx`）。

- **概览页** `/` — 今日调用数/成本/token/延迟/错误率 + 最近 50 条调用
- **调用详情** `/call/<id>` — 完整 prompt/completion JSON、token 明细
- **统计页** `/stats` — 按模型分组 + 14 天每日趋势柱状图
- **设置页** `/settings` — Web 界面修改上游配置

## 测试

```bash
cd backend
pip install -r requirements-dev.txt
pytest tests/ -v
```

## 项目结构

```
hermes-monitor/
├── backend/                # FastAPI 后端
│   ├── app/
│   │   ├── main.py         # 入口，路由注册
│   │   ├── config.py       # 配置管理（env + DB 双层）
│   │   ├── db.py           # SQLite 连接 + WAL 模式
│   │   ├── models.py       # ORM 模型 (Generation, Trace, Session)
│   │   ├── proxy.py        # ★ 核心：OpenAI 兼容反向代理
│   │   ├── recording.py    # 写入服务（proxy + SDK 共用）
│   │   ├── aggregation.py  # 统计聚合查询
│   │   ├── cost.py         # 费用计算 + 价格表
│   │   ├── tokens.py       # Token 估算（三层策略）
│   │   ├── auth.py         # 访问控制
│   │   ├── pages.py        # 看板页面路由
│   │   ├── ingestion.py    # SDK 上报端点
│   │   ├── settings_service.py  # 设置持久化
│   │   └── api/            # JSON API
│   │       ├── stats.py    # 统计 API
│   │       ├── traces.py   # 追踪 API
│   │       └── settings.py # 设置 API
│   ├── templates/          # Jinja2 看板模板
│   │   ├── base.html       # 基础布局
│   │   ├── overview.html   # 概览页（含时间本地化 JS）
│   │   ├── detail.html     # 调用详情页
│   │   ├── stats.html      # 统计页
│   │   └── settings.html   # 设置页
│   ├── tests/              # 单元/集成测试
│   ├── Dockerfile
│   └── requirements.txt
├── sdk/                    # 可选 Python SDK
├── fpk/                    # 飞牛 .fpk 打包源
└── docs/
    └── HERMES_SETUP.md     # Hermes Agent 接入详细说明
```

## 许可

MIT