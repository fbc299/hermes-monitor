# 更新说明

## 2026-06-21 — 监控看板增强版

本次更新把 Hermes Monitor 从“基础 LLM 代理监控”升级为更完整的个人 LLM 网关运维面板，重点解决三类问题：不用手写配置、能快速判断上游是否可用、能导出和保护调用数据。

### 一、可视化配置：不再手写 JSON

设置页 `/settings` 已改为卡片式可视化表单：

- 支持通过页面添加多个 OpenAI 兼容上游 Provider。
- 内置 OpenAI、OpenRouter、DeepSeek、通义千问、自定义模板。
- 每个上游可配置：名称、接口地址、API Key、模型匹配规则。
- 模型匹配支持通配符，例如 `gpt-*`、`qwen-*`、`deepseek-*`、`*`。
- 页面会自动生成 `UPSTREAMS_JSON`，高级区域仅用于查看和排查，不要求用户手写。
- 保存后配置立即生效，并持久化到数据库，重启服务后仍保留。

涉及文件：

- `backend/templates/settings.html`
- `backend/app/settings_service.py`
- `backend/app/config.py`
- `backend/app/upstreams.py`

### 二、多上游与模型路由

新增多 Provider 路由能力：

- 请求会根据 body 中的 `model` 自动选择匹配的上游。
- 命中第一个匹配规则；没有匹配时使用 `*` 或空规则的默认上游。
- 旧版 `UPSTREAM_BASE_URL` / `UPSTREAM_API_KEY` 仍兼容。
- `/v1/models` 会并行请求所有上游并合并模型列表。
- 单个上游失败不会影响其他上游，失败信息会放入 `upstream_errors`。
- 每条调用记录会保存实际命中的 `provider` 和 `base_url`，便于后续统计。

新增/更新测试覆盖：

- `backend/tests/test_proxy.py`
- `backend/tests/test_providers.py`

### 三、Provider 健康检查

设置页新增两个诊断按钮：

- `测试所有上游`：检查每个 Provider 的 `/v1/models` 是否可用。
- `刷新模型列表`：拉取模型并展示模型到 Provider 的路由关系。

新增 API：

- `GET /api/v1/providers/health`
- `GET /api/v1/providers/models`

健康检查返回内容包括：

- Provider 名称
- 接口地址
- 是否可用
- HTTP 状态码
- 模型数量
- 请求延迟
- 错误信息

涉及文件：

- `backend/app/api/providers.py`
- `backend/templates/settings.html`

### 四、错误中心

新增错误中心页面 `/errors`，用于集中排查上游故障和代理错误。

页面能力：

- 展示总错误数。
- 按 Provider 聚合错误。
- 按模型聚合错误。
- 按错误消息聚合 Top 10。
- 展示最近错误列表，并可跳转到单次调用详情。

新增 API：

- `GET /api/v1/errors`

涉及文件：

- `backend/app/api/errors.py`
- `backend/templates/errors.html`
- `backend/templates/base.html`
- `backend/app/aggregation.py`
- `backend/app/pages.py`

新增测试：

- `backend/tests/test_errors.py`

### 五、数据导出

概览页 `/` 新增导出按钮：

- `导出 CSV`
- `导出 JSON`

新增 API：

- `GET /api/v1/export/traces.csv`
- `GET /api/v1/export/traces.json`

CSV 用于表格、对账和快速分析，包含扁平字段：

- 调用 ID
- 时间
- Provider
- Base URL
- 模型
- 状态
- 输入/输出/总 token
- 费用
- 延迟
- 首 token 延迟
- 错误信息
- Trace ID

JSON 保留最近调用的结构化数据，适合备份和二次分析。

涉及文件：

- `backend/app/api/export.py`
- `backend/templates/overview.html`

新增测试：

- `backend/tests/test_export.py`

### 六、自动刷新大屏

概览页新增 `自动刷新 10 秒` 开关：

- 适合放在监控屏或 NAS 看板页面上。
- 开关状态保存在浏览器 `localStorage`。
- 开启后页面每 10 秒自动刷新。
- 关闭后不会继续刷新。

涉及文件：

- `backend/templates/overview.html`

### 七、隐私保存开关

设置页新增 `保存 Prompt/Completion` 配置：

- `保存完整内容`：保留原有行为，记录 prompt 和 completion。
- `只保存指标`：只记录 token、费用、延迟、状态、Provider 等指标，不保存请求和回答正文。

新配置键：

- `PAYLOAD_STORAGE_MODE=full`
- `PAYLOAD_STORAGE_MODE=metrics_only`

`metrics_only` 模式下，数据库中的 `prompt_json` 和 `completion_json` 会保存为脱敏占位：

```json
{"_redacted": true, "reason": "metrics_only"}
```

这样可以继续保留统计能力，同时减少敏感内容落库风险。

涉及文件：

- `backend/app/config.py`
- `backend/app/settings_service.py`
- `backend/app/recording.py`
- `backend/templates/settings.html`

新增测试：

- `backend/tests/test_privacy.py`

### 八、模板路径稳定性修复

修复了模板目录依赖当前工作目录的问题：

- 原来 `Jinja2Templates(directory="templates")` 在不同启动目录下可能找不到模板。
- 现在改为基于 `pages.py` 文件位置解析绝对模板路径。

涉及文件：

- `backend/app/pages.py`

### 九、验证记录

本次功能上线前后执行过以下验证：

- 后端测试：`39 passed, 1 warning`
- 服务重启：`hermes-monitor.service` 状态为 `active`
- 设置页 `/settings` 返回 `200 OK`
- 错误中心 `/errors` 返回 `200 OK`
- Provider 健康检查 API 返回 `200 OK`
- Provider 模型列表 API 返回 `200 OK`
- CSV 导出 API 返回 `200 OK`
- JSON 导出 API 返回 `200 OK`
- 当前真实上游 `apihub.agnes-ai.com` 健康，并可拉取模型列表

### 十、已知注意事项

- 设置页保存的数据库配置优先级高于环境变量。
- 如果在 systemd 或 Docker 中改了环境变量，但数据库中已有同名配置，实际生效值仍以数据库为准。
- 切换到 `metrics_only` 只影响之后新写入的调用记录，不会自动清理历史 prompt/completion。
- CSV/JSON 导出默认导出最近调用记录，接口支持 `limit` 参数。
- 当前导出接口仍受 `ACCESS_TOKEN` 保护；设置了访问口令时需要携带 `?token=` 或 Authorization 头。

## 相关提交

- `892a7e0` — `Add monitor diagnostics, exports, and privacy controls`
