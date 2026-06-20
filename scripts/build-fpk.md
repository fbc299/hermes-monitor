# 构建 & 打包指南

## 构建 Docker 镜像

```bash
cd hermes-monitor/backend
docker build -t hermes-monitor:latest .
```

### 推送到 Docker Hub（推荐）

```bash
docker tag hermes-monitor:latest your-dockerhub-user/hermes-monitor:v0.1.0
docker push your-dockerhub-user/hermes-monitor:v0.1.0
```

### 或导出为本地 tar

```bash
docker save hermes-monitor:latest -o hermes-monitor.tar
```

## 打包飞牛 .fpk

### 前置条件

1. 安装 [fnpack](https://developer.fnnas.com/) 工具（飞牛官方打包工具）
2. 准备好 Docker 镜像（已推送到 Docker Hub 或本地可用）

### 步骤

```bash
# 1. 创建打包项目（使用 fnpack 脚手架）
fnpack create hermes-monitor -t docker

# 2. 用本仓库 fpk/ 目录的内容覆盖脚手架生成的文件：
#    - manifest        → fpk/manifest
#    - app/docker/docker-compose.yaml → fpk/app/docker/docker-compose.yaml
#    - app/ui/config    → fpk/app/ui/config
#    - cmd/*            → fpk/cmd/*
#    - config/*         → fpk/config/*

# 3. 如果镜像已推送到 Docker Hub，编辑 manifest 或 compose 中的占位符：
#    DOCKERHUB_REPO = your-dockerhub-user/hermes-monitor
#    IMAGE_TAG = v0.1.0
#    VERSION = 0.1.0

# 4. 打包
fnpack pack hermes-monitor

# 产出: hermes-monitor-<version>.x86_64.fpk
```

### 在飞牛上安装

1. 将 `.fpk` 文件复制到飞牛 NAS
2. 打开「应用中心」→ 右下角「手动安装」
3. 选择 `.fpk` 文件 → 安装
4. 安装完成后，在 Docker 管理器中编辑容器环境变量配置上游

### 环境变量配置（关键）

在飞牛 Docker 管理器中编辑 `hermes-monitor` 容器，设置：

| 变量 | 值 |
|------|-----|
| `UPSTREAM_BASE_URL` | 你的 LLM 提供商地址 |
| `UPSTREAM_API_KEY` | 你的 API Key |
| `ACCESS_TOKEN` | （可选）看板访问口令 |

修改后重启容器即可生效。

## 本地测试（不打包）

```bash
cd backend
pip install -r requirements-dev.txt
pytest                        # 运行测试
UPSTREAM_BASE_URL=https://openrouter.ai/api/v1 \
UPSTREAM_API_KEY=sk-xxx \
python -m app.main            # 启动服务
```
