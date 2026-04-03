# Synapse 项目新手完整指南

这份文档是给第一次接触这个项目的人看的。目标很简单：

- 你能在 10 分钟内看懂这个项目到底在做什么。
- 你知道每个核心功能在哪个文件里实现。
- 你知道遇到问题应该先看哪里、改哪里。

如果你只记住一句话：

> Synapse 是一个“任务编排样例项目”，重点是任务生命周期管理，不是一次性模型调用。

---

## 1. 这个项目是干什么的？

Synapse 把“用户请求模型回答”拆成一个可管理的任务流程：

1. 用户提交任务（不是同步等待结果）。
2. 任务进入队列，后台 Worker 异步执行。
3. 执行过程持续产出事件（started/token/completed/failed...）。
4. 前端通过 SSE 实时看到事件流。
5. 失败可重试，重试耗尽进入死信，支持人工重放。
6. 任务可取消，且取消有幂等语义。

简单理解：这是一个“有任务状态机、有队列、有事件流、有重试治理”的 Agent 运行框架。

---

## 2. 一图看懂架构

```text
Web / API Client
   |
   | HTTP + SSE
   v
Gateway (Go)
  - API Handler
  - Worker (TaskProcessor)
  - Store (Postgres / InMemory)
  - Queue (Redis / InMemory)
   |
   | gRPC stream
   v
AI Engine (Python)
  - AgentRuntimeService
  - AgentRuntime (mock / openai-compatible)
```

核心思想：

- Gateway 负责“任务系统能力”（状态、队列、重试、取消、死信、SSE）。
- AI Engine 只负责“执行任务并流式产出模型事件”。

---

## 3. 技术栈（按层）

## 3.1 前端

- React 19 + TypeScript + Vite
- 目录：`apps/web`
- 作用：任务创建、任务列表、实时事件流、取消、批量取消、死信重放

## 3.2 网关后端

- Go 1.25
- 标准库 `net/http` 路由
- gRPC 客户端（调用 AI Engine）
- Redis 队列（可选）+ 内存队列（降级）
- PostgreSQL 存储（可选）+ 内存存储（降级）
- 目录：`services/gateway-go`

## 3.3 AI 引擎

- Python 3.12
- `grpcio` / `grpcio-tools`
- 通过 OpenAI 兼容 HTTP 接口调用上游模型（不依赖 OpenAI SDK）
- 支持 `mock` provider 本地演示
- 目录：`services/ai-engine-py`

## 3.4 协议与编排

- Protobuf + gRPC：`proto/synapse/v1/agent.proto`
- Docker Compose：`docker-compose.yml`
- 开发脚本（PowerShell）：`scripts/dev.ps1`
- 生成脚本（补 `__init__.py`）：`scripts/post_gen.py`

---

## 4. 仓库目录怎么读（新手版）

```text
.
├── apps/web                    # 前端控制台
├── doc                         # 项目文档
├── proto/synapse/v1            # gRPC 协议定义
├── scripts                     # 本地开发与生成脚本
├── services/gateway-go         # Go 网关（HTTP + Worker）
└── services/ai-engine-py       # Python AI 引擎（gRPC）
```

建议阅读顺序：

1. 先看这份文档（你正在看）。
2. 再看 `doc/architecture.md`（理解状态机和时序）。
3. 再看 `doc/api.md`（理解接口契约）。
4. 最后看代码实现。

---

## 5. 文件级功能地图（重点）

下面是“这个文件具体干什么”的速查表。

## 5.1 根目录与基础设施

| 文件 | 作用 | 你什么时候会改它 |
| --- | --- | --- |
| `README.md` | 项目总介绍与快速启动 | 新增功能后同步说明 |
| `docker-compose.yml` | 一键拉起 gateway、ai-engine、postgres、redis | 改服务端口、环境变量、镜像 |
| `docker-compose.zhipu.env.example` | 智谱兼容模式配置模板 | 切换模型提供方示例 |
| `docker-compose.mirror.env.example` | 镜像代理配置模板（网络受限） | 拉镜像慢/失败时 |
| `scripts/dev.ps1` | Windows 本地开发统一入口（proto/gateway/ai/web/up/down） | 增加新任务命令 |
| `Makefile` | 类 Unix 下的同类命令入口 | Linux/macOS 团队协作 |

## 5.2 协议层（网关与 AI 引擎共享）

| 文件 | 作用 |
| --- | --- |
| `proto/synapse/v1/agent.proto` | 定义 gRPC 服务 `AgentRuntime`、请求/响应结构、事件枚举 |
| `services/gateway-go/internal/gen/synapse/v1/agent.pb.go` | Go 端自动生成消息代码（不要手改） |
| `services/gateway-go/internal/gen/synapse/v1/agent_grpc.pb.go` | Go 端自动生成 gRPC Stub（不要手改） |
| `services/ai-engine-py/synapse/v1/agent_pb2.py` | Python 端自动生成消息代码（不要手改） |
| `services/ai-engine-py/synapse/v1/agent_pb2_grpc.py` | Python 端自动生成 gRPC Stub（不要手改） |

## 5.3 网关（Go）

### 启动与依赖装配

| 文件 | 作用 |
| --- | --- |
| `services/gateway-go/cmd/server/main.go` | 进程入口：加载配置、连 AI、初始化 Store/Queue、启动 Worker 与 HTTP 服务、优雅退出 |
| `services/gateway-go/internal/config/config.go` | 环境变量读取与默认值回退 |

### API 层

| 文件 | 作用 |
| --- | --- |
| `services/gateway-go/internal/api/router.go` | 注册全部 HTTP 路由 + 请求日志中间件 |
| `services/gateway-go/internal/api/handlers.go` | 具体端点实现：创建、查询、取消、批量取消、重放、SSE、死信列表、健康检查 |
| `services/gateway-go/internal/api/handlers_cancel_test.go` | 取消语义测试：202/200/409、批量取消部分成功 |

### 任务执行与重试

| 文件 | 作用 |
| --- | --- |
| `services/gateway-go/internal/worker/processor.go` | Worker 核心：出队、调用 gRPC、状态迁移、重试、取消收敛、死信写入 |
| `services/gateway-go/internal/worker/processor_cancel_test.go` | 取消终结逻辑测试 |
| `services/gateway-go/internal/worker/processor_retry_test.go` | 可重试/不可重试错误策略测试 |
| `services/gateway-go/internal/worker/processor_race_test.go` | 运行中取消并发场景测试 |

### 存储、队列、领域模型

| 文件 | 作用 |
| --- | --- |
| `services/gateway-go/internal/domain/task.go` | 任务、事件、死信实体定义 |
| `services/gateway-go/internal/store/store.go` | TaskStore 抽象接口 |
| `services/gateway-go/internal/store/inmemory.go` | 内存存储实现（开发/降级） |
| `services/gateway-go/internal/store/postgres.go` | PostgreSQL 实现（含自动建表） |
| `services/gateway-go/internal/queue/queue.go` | TaskQueue 抽象接口 |
| `services/gateway-go/internal/queue/inmemory.go` | 内存队列实现 |
| `services/gateway-go/internal/queue/redis.go` | Redis 队列实现（LPUSH/BRPOP） |
| `services/gateway-go/internal/agent/client.go` | AI 引擎 gRPC 客户端封装 |

### 依赖与镜像

| 文件 | 作用 |
| --- | --- |
| `services/gateway-go/go.mod` | Go 依赖（uuid、pq、redis、grpc） |
| `services/gateway-go/Dockerfile` | Gateway 镜像构建（含 proto 生成与静态编译） |

## 5.4 AI 引擎（Python）

| 文件 | 作用 |
| --- | --- |
| `services/ai-engine-py/app/main.py` | gRPC 服务启动入口 |
| `services/ai-engine-py/app/config.py` | AI 引擎环境变量读取与默认值 |
| `services/ai-engine-py/app/service.py` | gRPC Service 实现：把 runtime 输出转成事件流 |
| `services/ai-engine-py/app/runtime.py` | provider 运行时：`mock` / `openai`，含重试退避 |
| `services/ai-engine-py/requirements.txt` | Python 依赖（grpcio） |
| `services/ai-engine-py/Dockerfile` | AI 引擎镜像构建（含 proto 生成） |

## 5.5 前端（React）

| 文件 | 作用 |
| --- | --- |
| `apps/web/src/main.tsx` | 前端入口，挂载 `App` |
| `apps/web/src/App.tsx` | 主要业务页面：双视图、双语、任务管理、SSE、批量取消历史 |
| `apps/web/src/App.css` | 页面组件样式 |
| `apps/web/src/index.css` | 全局样式与字体 |
| `apps/web/vite.config.ts` | 开发代理（`/v1`、`/healthz` 转发到网关） |
| `apps/web/package.json` | 前端依赖与脚本 |

---

## 6. 一个请求到底怎么跑（端到端）

以“创建任务并看到 token”为例。

1. 前端 `App.tsx` 调 `POST /v1/tasks`。
2. 网关 `handlers.go/CreateTask` 校验参数并写入 Store（状态 `queued`）。
3. 同一接口把 task_id 入队（Queue）。
4. Worker `processor.go/Run` 出队拿到 task_id。
5. Worker 把状态改为 `running`，然后用 `agent/client.go` 调 AI 引擎 gRPC `SubmitTask`。
6. AI 引擎 `service.py` 先发 `started`，再从 `runtime.py` 逐 token 产出 `token`，最后发 `completed`。
7. Worker 收到每个事件都 `AppendEvent` 持久化到 Store。
8. 前端用 EventSource 订阅 `GET /v1/tasks/{taskID}/events`，网关从 Store 轮询增量事件并推送 SSE。
9. 任务终态后，SSE 发送 `terminal`，前端关闭连接。

---

## 7. 任务状态机（小白重点）

状态：

- `queued`：已创建，等待执行
- `running`：正在执行
- `completed`：成功完成
- `failed`：失败（可能已经重试到上限）
- `canceled`：被取消

常见流转：

- `queued -> running -> completed`
- `queued/running -> canceled`
- `running -> failed`（若失败可重试）
- `failed -> queued`（通过 replay 重放）

冲突规则：

- `completed/failed` 不能取消（409）。
- `running` 不能重放（409）。

---

## 8. 你最常做的 6 件事

## 8.1 启动全栈（Docker）

```powershell
.\scripts\dev.ps1 -Task up-zhipu-mirror
```

## 8.2 本地分服务启动

```powershell
.\scripts\dev.ps1 -Task ai
.\scripts\dev.ps1 -Task gateway
.\scripts\dev.ps1 -Task web
```

## 8.3 创建任务

```bash
curl -X POST http://127.0.0.1:8080/v1/tasks \
  -H "Content-Type: application/json" \
  -d '{"user_id":"demo-user","prompt":"Draft a release checklist"}'
```

## 8.4 看事件流

```bash
curl -N "http://127.0.0.1:8080/v1/tasks/<task-id>/events"
```

## 8.5 取消任务

```bash
curl -X POST "http://127.0.0.1:8080/v1/tasks/<task-id>/cancel" \
  -H "Content-Type: application/json" \
  -d '{"requested_by":"ops","reason":"manual stop"}'
```

## 8.6 重放死信任务

```bash
curl -X POST "http://127.0.0.1:8080/v1/tasks/<task-id>/replay"
```

---

## 9. 如果你要改功能，先改哪？

## 9.1 新增一个 HTTP 字段

1. 改 `services/gateway-go/internal/api/handlers.go` 请求结构体与校验。
2. 改 `services/gateway-go/internal/domain/task.go`（如果要入库）。
3. 改 `store` 实现（内存 + Postgres）。
4. 改前端 `apps/web/src/App.tsx`。
5. 补测试：`handlers_cancel_test.go` 同风格。

## 9.2 新增一个 gRPC 事件类型

1. 改 `proto/synapse/v1/agent.proto` 枚举。
2. 重新生成 proto（`scripts/dev.ps1 -Task proto`）。
3. 改 AI 引擎 `service.py` 产出事件。
4. 改网关 `processor.go` 映射与状态迁移逻辑。
5. 改前端 `App.tsx` 事件类型展示。

## 9.3 改重试策略

1. 改 `services/gateway-go/internal/worker/processor.go`。
2. 补/改 `processor_retry_test.go`。
3. 必要时同步 `doc/architecture.md` 与 `doc/api.md`。

---

## 10. 常见排错路径

## 10.1 `/healthz` 是 degraded

看哪里：

1. AI 引擎是否启动（`services/ai-engine-py/app/main.py`）。
2. 网关配置的 `SYNAPSE_AI_ENGINE_ADDR` 是否正确（`config.go`）。
3. Compose 网络里是否可达 `ai-engine:50051`（`docker-compose.yml`）。

## 10.2 任务一直 queued 不动

看哪里：

1. 队列是否可用（`queue/inmemory.go` 或 `queue/redis.go`）。
2. Worker 是否在跑（`processor.go/Run`）。
3. 是否入队失败被立即标记 failed（`handlers.go/CreateTask`）。

## 10.3 SSE 没有事件

看哪里：

1. `handlers.go/StreamTaskEvents`。
2. `store.ListEvents` 是否有数据。
3. 前端 EventSource 是否带了正确 task_id（`App.tsx`）。

---

## 11. 术语表（零基础版）

- gRPC：一种高性能 RPC 协议，这里用于 Gateway 调 AI Engine。
- Protobuf：接口和消息格式定义语言（`agent.proto`）。
- SSE：服务端推送事件流，前端持续接收任务进度。
- Dead Letter（死信）：重试耗尽仍失败的任务集合。
- Replay（重放）：把失败/取消过的任务重新排队执行。
- Idempotent（幂等）：重复调用不会产生额外副作用（如已取消再取消）。

---

## 12. 文档同步建议

后续每次改动至少同步这三类文档：

1. 接口变更：更新 `doc/api.md`
2. 配置变更：更新 `doc/configuration.md`
3. 行为变更（重试/状态机）：更新 `doc/architecture.md`

如果改动面向新人 onboarding，再同步这份文档。
