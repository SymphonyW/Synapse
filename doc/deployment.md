# 开发与部署指南

本文档覆盖当前代码可直接执行的开发、联调、部署与排障流程。

## 1. 前置条件

- Go 1.25+
- Python 3.12+
- Node.js 20+
- protoc（Protocol Buffers 编译器）
- Docker Desktop（使用 Compose 时需要）

默认端口：

- Gateway：8080
- AI Engine：50051
- PostgreSQL：5432
- Redis：6379
- Web：5173

## 2. 方案一：Docker Compose 一键联调

### 2.1 使用 dev 脚本（Windows 友好）

脚本文件：`scripts/dev.ps1`

可用任务：

- `up`：`docker compose up --build`（前台）
- `up-openai`：读取 `docker-compose.openai.env`（后台）
- `up-gemini`：读取 `docker-compose.gemini.env`（后台）
- `up-zhipu`：读取 `docker-compose.zhipu.env`（后台）
- `up-zhipu-mirror`：读取 `docker-compose.mirror.env` + `docker-compose.zhipu.env`（后台）
- `down`：停止并删除容器

示例（OpenAI）：

```powershell
Copy-Item docker-compose.openai.env.example docker-compose.openai.env
# 编辑 docker-compose.openai.env，填入真实 key
.\scripts\dev.ps1 -Task up-openai
```

停止：

```powershell
.\scripts\dev.ps1 -Task down
```

### 2.2 直接使用 docker compose

标准启动：

```bash
docker compose up --build -d
```

镜像代理：

```bash
docker compose --env-file docker-compose.mirror.env up --build -d
```

Gemini（兼容接口）：

```bash
docker compose --env-file docker-compose.gemini.env up --build -d
```

Zhipu（兼容接口）：

```bash
docker compose --env-file docker-compose.zhipu.env up --build -d
```

镜像代理 + Zhipu：

```bash
docker compose --env-file docker-compose.mirror.env --env-file docker-compose.zhipu.env up --build -d
```

## 3. 方案二：本地分服务运行

### 3.1 生成协议代码

Windows：

```powershell
.\scripts\dev.ps1 -Task proto
```

或使用 Make：

```bash
make proto
```

### 3.2 安装依赖

AI 引擎依赖：

```powershell
Push-Location services/ai-engine-py
pip install -r requirements.txt
Pop-Location
```

前端依赖：

```powershell
Push-Location apps/web
npm install
Pop-Location
```

### 3.3 启动服务

分别在多个终端运行：

```powershell
.\scripts\dev.ps1 -Task ai
.\scripts\dev.ps1 -Task gateway
.\scripts\dev.ps1 -Task web
```

### 3.4 本地切换 OpenAI 兼容模型（可选）

```powershell
$env:SYNAPSE_MODEL_PROVIDER='openai'
$env:SYNAPSE_OPENAI_API_KEY='<your-api-key>'
# 可选：$env:SYNAPSE_OPENAI_BASE_URL='https://api.openai.com/v1'
# 可选：$env:SYNAPSE_OPENAI_MODEL='gpt-4o-mini'
```

## 4. 快速验收

### 4.1 健康检查

```bash
curl http://127.0.0.1:8080/healthz
```

### 4.2 创建任务

```bash
curl -X POST http://127.0.0.1:8080/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{"user_id":"demo-user","prompt":"Draft a release checklist"}'
```

### 4.3 订阅事件流

```bash
curl -N "http://127.0.0.1:8080/v1/tasks/<task-id>/events"
```

### 4.4 取消任务

```bash
curl -X POST "http://127.0.0.1:8080/v1/tasks/<task-id>/cancel" \
  -H "Content-Type: application/json" \
  -d '{"requested_by":"ops","reason":"manual stop"}'
```

### 4.5 死信列表

```bash
curl "http://127.0.0.1:8080/v1/dead-letters?limit=20"
```

## 5. 测试命令

Gateway 单元测试：

```bash
cd services/gateway-go
go test ./...
```

前端构建检查：

```bash
cd apps/web
npm run build
```

## 6. 常见问题排查

### 6.1 /healthz 返回 degraded

排查顺序：

1. AI 引擎是否启动。
2. `SYNAPSE_AI_ENGINE_ADDR` 是否正确。
3. Compose 场景下网关到 `ai-engine:50051` 是否可达。

### 6.2 创建成功但任务不推进

排查项：

1. Worker 是否运行（网关日志）。
2. Redis 不可用是否已回退内存队列。
3. AI 引擎日志是否报错。

### 6.3 事件流没有输出

排查项：

1. 是否使用正确 taskID。
2. SSE 请求是否 200 且 `Content-Type` 为 `text/event-stream`。
3. 任务是否已经进入终态并发送 `terminal`。

### 6.4 Compose 拉镜像失败

可尝试：

1. 使用 `docker-compose.mirror.env`。
2. 检查 Docker 代理。
3. `docker login` 后重试。
