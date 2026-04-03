# API 文档（HTTP + SSE + gRPC）

本文档描述当前代码实现的全部对外契约。

## 1. 通用约定

### 1.1 基础地址

- 网关默认地址：`http://127.0.0.1:8080`
- AI 引擎默认 gRPC 地址：`127.0.0.1:50051`

### 1.2 错误格式

所有错误响应统一为：

```json
{
  "error": "..."
}
```

### 1.3 任务状态

- `queued`
- `running`
- `completed`
- `failed`
- `canceled`

## 2. 数据结构

### 2.1 Task

```json
{
  "id": "uuid",
  "user_id": "u-1001",
  "prompt": "Draft a release checklist",
  "status": "queued",
  "error": "",
  "metadata": {
    "source": "web-console"
  },
  "created_at": "2026-04-03T10:00:00Z",
  "updated_at": "2026-04-03T10:00:00Z"
}
```

说明：

- `error`、`metadata` 在空值时可能省略。

### 2.2 DeadLetterTask

```json
{
  "task_id": "uuid",
  "reason": "upstream timeout",
  "attempts": 3,
  "created_at": "2026-04-03T10:00:00Z",
  "updated_at": "2026-04-03T10:10:00Z"
}
```

### 2.3 SSE 事件载荷

```json
{
  "event_id": 12,
  "type": "token",
  "message": "",
  "token": "Synapse",
  "trace_id": "uuid",
  "emitted_at_unix_ms": 1775182310296
}
```

## 3. HTTP 接口

### 3.1 GET /healthz

用途：检查网关到 AI 引擎的连通性。

成功（200）：

```json
{
  "status": "ok",
  "ai_engine": "ok",
  "model_provider": "mock"
}
```

失败（503）：

```json
{
  "status": "degraded",
  "error": "rpc error: ..."
}
```

### 3.2 POST /v1/tasks

用途：创建任务并入队。

请求体：

```json
{
  "user_id": "u-1001",
  "prompt": "Draft a release checklist",
  "metadata": {
    "source": "web-console"
  }
}
```

约束：

- `user_id`、`prompt` 去空白后不能为空。
- 拒绝未知字段（`DisallowUnknownFields`）。

返回：

- 201：返回新建 `Task`（初始状态 `queued`）。
- 400：请求体非法。
- 409：任务 ID 冲突（极少见）。
- 500：落库或入队失败。

### 3.3 GET /v1/tasks

用途：查询任务列表（按 `updated_at` 倒序）。

查询参数：

- `limit`：默认 50，最大 500。
- `status`：可选，必须是合法状态之一。

成功（200）：

```json
{
  "items": [],
  "count": 0
}
```

错误：

- 400：`limit` 或 `status` 非法。
- 500：查询失败。

### 3.4 GET /v1/tasks/{taskID}

用途：查询单任务。

返回：

- 200：`Task`
- 404：任务不存在

### 3.5 POST /v1/tasks/{taskID}/cancel

用途：取消单任务。

请求体可选：

```json
{
  "requested_by": "ops-console",
  "reason": "manual stop"
}
```

语义：

- `queued/running`：
  - 更新任务到 `canceled`
  - 追加 `cancel_requested` 事件
  - 返回 202
- `canceled`：幂等返回当前任务，返回 200
- `completed/failed`：返回 409
- 不存在：返回 404

说明：

- 请求体可为空；未知字段会被拒绝（400）。

### 3.6 POST /v1/tasks/cancel

用途：批量取消任务。

请求体：

```json
{
  "task_ids": ["id-1", "id-2", "id-3"],
  "requested_by": "ops-console",
  "reason": "maintenance"
}
```

返回（始终 200，前提是请求体合法）：

```json
{
  "requested": 3,
  "canceled_count": 2,
  "already_canceled_count": 1,
  "failed_count": 1,
  "canceled": [
    {
      "id": "id-1",
      "status": "canceled"
    }
  ],
  "failed": [
    {
      "task_id": "id-3",
      "error": "task already in terminal state"
    }
  ]
}
```

关键细节：

- `task_ids` 不能为空，否则 400。
- 同一请求内，重复 task_id 仅处理一次。
- 空白 task_id 会被忽略。
- `requested` 统计原始数组长度（包含重复/空白项）。
- `canceled` 数组包含新取消 + 已取消（幂等）任务，并按首见顺序返回。

### 3.7 POST /v1/tasks/{taskID}/replay

用途：重放任务。

语义：

- `running`：返回 409。
- 其他状态（含 `queued/completed/failed/canceled`）：
  - 状态更新为 `queued`
  - 清空 `error`
  - 清理死信
  - 追加 `replay_requested` 事件
  - 重新入队
  - 返回 202

错误：

- 404：任务不存在
- 500：重放入队失败

### 3.8 GET /v1/tasks/{taskID}/events（SSE）

用途：订阅任务事件流。

查询参数：

- `last_event_id`：可选，默认 0，必须是非负整数。

连接建立后行为：

1. 先发送 `info` 事件，消息为 `stream_opened`。
2. 每 300ms 拉取一次 `task_events` 增量数据（最多 200 条/轮）。
3. 任务进入终态且本轮无新增事件时，发送 `terminal` 并关闭连接。

SSE 示例：

```text
event: token
data: {"event_id":12,"type":"token","token":"Synapse","trace_id":"...","emitted_at_unix_ms":1770000000000}
```

终态事件示例：

```text
event: terminal
data: {"task_id":"...","status":"completed"}
```

错误语义：

- 建连前任务不存在：HTTP 404 JSON。
- 流中任务消失或查询失败：发送 `failed` 事件后关闭连接。

### 3.9 GET /v1/dead-letters

用途：查询死信列表。

参数：

- `limit`：默认 100，最大 500。

成功响应：

```json
{
  "items": [],
  "count": 0
}
```

## 4. SSE 事件类型

已实现事件类型：

- `info`
- `started`
- `token`
- `completed`
- `failed`
- `cancel_requested`
- `canceled`
- `replay_requested`
- `dead_lettered`
- `unspecified`
- `terminal`（仅 SSE 层）

## 5. gRPC 契约

来源：`proto/synapse/v1/agent.proto`

### 5.1 AgentRuntime.Health

- 请求：`HealthRequest`（空）
- 响应：`HealthResponse`
  - `status`
  - `model_provider`

### 5.2 AgentRuntime.SubmitTask

- 请求：`SubmitTaskRequest`
  - `task_id`
  - `user_id`
  - `prompt`
  - `metadata`
- 响应：`stream AgentEvent`

`AgentEvent` 字段：

- `type`（枚举）
- `message`
- `token`
- `trace_id`
- `emitted_at_unix_ms`

网关在持久化时会把枚举名标准化为小写字符串，例如 `AGENT_EVENT_TYPE_STARTED` -> `started`。

## 6. 幂等与冲突语义速查

- 单任务取消：已取消任务幂等（200）。
- 批量取消：同一请求内自动去重同一个 task_id。
- 重放：仅 `running` 冲突（409）；其他状态可重放（202）。
