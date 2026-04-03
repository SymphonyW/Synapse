# Synapse

Synapse 是一个面向 Agent 任务编排的工程化样例项目，当前实现采用双服务架构：

- Gateway（Go）：对外 HTTP API、任务队列消费、重试与死信、SSE 事件流。
- AI Engine（Python）：对内 gRPC Runtime，支持 mock 与 OpenAI 兼容模型接口。

项目重点不是“单次模型调用”，而是“任务生命周期管理”：创建、异步执行、流式观测、取消、重放、死信收敛。

## 当前实现能力

- 任务 API：创建、列表、详情、单任务取消、批量取消、重放。
- 事件 API：按任务 SSE 增量推流，支持 `last_event_id` 续传。
- 失败治理：Worker 有界重试，失败后进入死信；支持死信任务重放。
- 存储与队列：
  - PostgreSQL 可用时使用持久化存储，不可用时自动回退内存存储。
  - Redis 可用时使用 Redis 队列，不可用时自动回退内存队列。
- AI Runtime：
  - `SYNAPSE_MODEL_PROVIDER=mock`：本地可预测 token 流。
  - `SYNAPSE_MODEL_PROVIDER=openai`：通过 OpenAI 兼容接口调用上游模型。
  - 支持 `gemini`、`zhipu` 语义别名（底层走 OpenAI 兼容通道）。
- Web 控制台（React + Vite）：
  - 用户端与运维端双视图。
  - 双语切换（中/英）。
  - 任务筛选、单/批取消、死信重放、实时事件流展示。

## 当前边界

- 尚未实现鉴权、租户隔离、配额、审计。
- Worker 当前为单进程内消费模型，不是分布式调度系统。
- 尚未实现任务优先级、延迟队列、暂停/恢复等高级调度能力。

## 仓库结构

```text
.
├── apps/web                    # React 控制台
├── doc                         # 项目文档
├── proto/synapse/v1            # gRPC 协议
├── scripts                     # 开发脚本
├── services/gateway-go         # Go 网关 + Worker
└── services/ai-engine-py       # Python gRPC AI 引擎
```

## 快速开始

### 方式 A：Docker Compose（推荐）

1. 选择 provider 配置文件（以下示例以 OpenAI 为例）：

```powershell
Copy-Item docker-compose.openai.env.example docker-compose.openai.env
# 编辑 docker-compose.openai.env，填入真实 API Key
```

2. 启动：

```powershell
.\scripts\dev.ps1 -Task up-openai
```

可选任务：

- `up`：`docker compose up --build`（前台）
- `up-openai`：使用 `docker-compose.openai.env`（后台）
- `up-gemini`：使用 `docker-compose.gemini.env`（后台）
- `up-zhipu`：使用 `docker-compose.zhipu.env`（后台）
- `up-zhipu-mirror`：镜像代理 + 智谱配置（后台）

3. 验证：

```bash
curl http://127.0.0.1:8080/healthz
```

4. 停止：

```powershell
.\scripts\dev.ps1 -Task down
```

### 方式 B：本地分服务运行

1. 生成 proto 代码：

```powershell
.\scripts\dev.ps1 -Task proto
```

2. 安装前端依赖：

```powershell
Push-Location apps/web
npm install
Pop-Location
```

3. 安装 Python 依赖：

```powershell
Push-Location services/ai-engine-py
pip install -r requirements.txt
Pop-Location
```

4. 分别在不同终端启动：

```powershell
.\scripts\dev.ps1 -Task ai
.\scripts\dev.ps1 -Task gateway
.\scripts\dev.ps1 -Task web
```

## 核心接口速览

- `GET /healthz`
- `POST /v1/tasks`
- `GET /v1/tasks`
- `GET /v1/tasks/{taskID}`
- `POST /v1/tasks/{taskID}/cancel`
- `POST /v1/tasks/cancel`
- `POST /v1/tasks/{taskID}/replay`
- `GET /v1/tasks/{taskID}/events`（SSE）
- `GET /v1/dead-letters`

AI 引擎 gRPC：

- `AgentRuntime.Health`
- `AgentRuntime.SubmitTask`（server streaming）

## 常用验证命令

创建任务：

```bash
curl -X POST http://127.0.0.1:8080/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{"user_id":"demo-user","prompt":"Draft a release checklist"}'
```

查询任务列表：

```bash
curl "http://127.0.0.1:8080/v1/tasks?limit=20"
```

订阅事件流：

```bash
curl -N "http://127.0.0.1:8080/v1/tasks/<task-id>/events"
```

取消任务：

```bash
curl -X POST "http://127.0.0.1:8080/v1/tasks/<task-id>/cancel" \
  -H "Content-Type: application/json" \
  -d '{"requested_by":"ops","reason":"manual stop"}'
```

## 文档导航

- 项目文档索引：`doc/README.md`
- 系统架构：`doc/architecture.md`
- API 细节：`doc/api.md`
- 配置说明：`doc/configuration.md`
- 部署与排障：`doc/deployment.md`
- 前端控制台：`apps/web/README.md`
