# 系统架构

本文档描述当前仓库已经实现的真实架构与运行语义。

## 1. 设计目标

- 异步执行：请求提交和任务执行解耦。
- 可观测：任务执行过程可通过事件流实时观察。
- 可恢复：失败任务支持有界重试并进入死信。
- 可降级：PostgreSQL/Redis 不可用时回退到内存实现。
- 可演进：Go 网关和 Python 运行时通过 gRPC 契约解耦。

## 2. 运行拓扑

```text
Client / Web
    |
    | HTTP + SSE
    v
Gateway (Go)
  - API Handler
  - Task Processor (Worker)
  - TaskStore (Postgres / InMemory)
  - TaskQueue (Redis / InMemory)
    |
    | gRPC stream
    v
AI Engine (Python)
  - AgentRuntimeService
  - AgentRuntime (mock / openai-compatible)
```

## 3. 组件职责

### 3.1 Gateway

启动入口在 `services/gateway-go/cmd/server/main.go`，职责包括：

- 加载环境变量配置。
- 建立到 AI 引擎的 gRPC 连接（启动阶段必须成功）。
- 初始化存储：默认内存，若 `SYNAPSE_DATABASE_URL` 可用则切到 PostgreSQL。
- 初始化队列：默认内存，若 `SYNAPSE_REDIS_ADDR` 可用则切到 Redis。
- 启动 Worker 循环。
- 挂载 HTTP 路由并提供优雅退出。

### 3.2 API 层

位置：`services/gateway-go/internal/api`。

核心能力：

- 任务创建、查询、取消、重放。
- 批量取消（支持部分成功）。
- 死信列表查询。
- SSE 事件流。
- 健康检查。

### 3.3 Worker（TaskProcessor）

位置：`services/gateway-go/internal/worker/processor.go`。

核心逻辑：

- 从队列阻塞拉取任务 ID。
- 按任务执行超时、最大重试次数、固定退避间隔执行。
- 调用 AI 引擎 `SubmitTask` 并持久化流式事件。
- 处理取消、失败、死信、终态收敛。

当前消费模型：单实例内串行处理（一个任务完成后再处理下一个）。

### 3.4 Store 抽象

接口：`TaskStore`。

实现：

- InMemory：开发与降级路径。
- Postgres：持久化路径，启动时自动建表。

持久化实体：

- `tasks`
- `task_events`
- `dead_letter_tasks`

### 3.5 Queue 抽象

接口：`TaskQueue`。

实现：

- InMemoryQueue：基于缓冲 channel。
- RedisQueue：`LPUSH` 入队、`BRPOP` 出队。

### 3.6 AI Engine

入口：`services/ai-engine-py/app/main.py`。

职责：

- 提供 gRPC `Health`。
- 提供 gRPC `SubmitTask` 流式输出 `started/token/completed`（异常时输出 `failed`）。

Runtime provider：

- `mock`
- `openai`（OpenAI 兼容接口）

语义别名：`gemini`、`zhipu` 最终映射到 `openai` 通道。

## 4. 关键时序

### 4.1 创建并执行任务

1. 客户端调用 `POST /v1/tasks`。
2. API 校验并落库 `queued`。
3. 任务 ID 入队。
4. Worker 出队并设置 `running`。
5. Worker 调用 AI 引擎 `SubmitTask`。
6. 每条流式事件落库到 `task_events`。
7. SSE 接口按 `last_event_id` 增量推送。
8. 收到 `completed` 或 EOF 补偿后更新 `completed`。

### 4.2 取消任务

1. 客户端调用单任务取消或批量取消接口。
2. API 将任务状态更新为 `canceled`，写入 `cancel_requested` 事件并清理死信。
3. API 尝试通知 Worker 取消活跃执行上下文。
4. Worker 收到取消后执行 `finalizeCanceled`：
   - 保留已有取消原因（若已有 `task.error`）。
   - 写入 `canceled` 事件。
   - 再次清理死信。

### 4.3 重试与死信

1. 单次执行失败且未达最大尝试次数：等待固定 `RetryBackoff` 后重试。
2. 重试轮次大于 1 时，追加 `info/retry_attempt` 事件。
3. 达到上限仍失败：
   - 状态置为 `failed`
   - 写入死信记录
   - 追加 `failed` 与 `dead_lettered` 事件

### 4.4 重放任务

1. 调用 `POST /v1/tasks/{taskID}/replay`。
2. 非 `running` 任务会被重置为 `queued` 并清空 `error`。
3. 清理死信并写入 `replay_requested` 事件。
4. 重新入队执行。

## 5. 状态机

任务状态集合：

- `queued`
- `running`
- `completed`
- `failed`
- `canceled`

主要迁移：

- `queued -> running`
- `running -> completed`
- `running -> failed`
- `queued/running -> canceled`
- `非 running -> queued`（replay）

冲突规则：

- `completed/failed` 不允许取消（HTTP 409）。
- `running` 不允许重放（HTTP 409）。

## 6. 事件模型

持久化事件类型（来自 API/Worker/AI 引擎）：

- `info`
- `started`
- `token`
- `completed`
- `failed`
- `cancel_requested`
- `canceled`
- `replay_requested`
- `dead_lettered`
- `unspecified`（枚举兜底）

SSE 额外事件：

- `terminal`：仅流式层发送，不落库。

## 7. 一致性与降级策略

- 创建任务采用“先落库后入队”。
- 若入队失败，任务会被立即标记为 `failed` 并记录事件，避免长期停留在 `queued`。
- SSE 通过查询持久化事件实现断线续传，不依赖 Worker 内存状态。
- 当 PostgreSQL/Redis 初始化失败，系统自动降级到内存实现，保证可启动。

## 8. 已知限制

- Worker 当前为单进程串行消费，吞吐扩展能力有限。
- 取消依赖进程内 `active` 映射，多副本场景需要额外协调机制。
- 未实现认证鉴权与多租户隔离。