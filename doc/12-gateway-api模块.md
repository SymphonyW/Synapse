# Gateway API 模块

Gateway API 模块位于 [services/gateway-go/internal/api](../services/gateway-go/internal/api)，负责 HTTP 路由、认证、任务控制、SSE、死信和长期记忆转发。

## 关键文件

| 文件 | 说明 |
|---|---|
| [router.go](../services/gateway-go/internal/api/router.go) | 注册全部 HTTP 路由和访问日志中间件 |
| [handlers.go](../services/gateway-go/internal/api/handlers.go) | 任务、会话、取消、审批、重放、SSE、死信 |
| [handlers_auth.go](../services/gateway-go/internal/api/handlers_auth.go) | 注册、登录、退出、当前用户 |
| [handlers_memory.go](../services/gateway-go/internal/api/handlers_memory.go) | 长期记忆 HTTP API 到 AI Engine Memory RPC 的转发 |

## 路由清单

| 方法 | 路径 | 处理函数 | 权限 |
|---|---|---|---|
| GET | `/healthz` | `Healthz` | 无 |
| POST | `/v1/auth/register` | `RegisterUser` | 无 |
| POST | `/v1/auth/login` | `LoginUser` | 无 |
| POST | `/v1/auth/logout` | `LogoutUser` | 可选 |
| GET | `/v1/auth/me` | `GetCurrentUser` | 登录 |
| GET | `/v1/tasks` | `ListTasks` | 登录 |
| POST | `/v1/tasks` | `CreateTask` | 登录 |
| GET | `/v1/tasks/{taskID}` | `GetTask` | owner/admin |
| GET | `/v1/tasks/{taskID}/events` | `StreamTaskEvents` | owner/admin |
| POST | `/v1/tasks/{taskID}/cancel` | `CancelTask` | owner/admin |
| POST | `/v1/tasks/cancel` | `BatchCancelTasks` | owner/admin |
| POST | `/v1/tasks/{taskID}/approve` | `ApproveTask` | owner/admin |
| POST | `/v1/tasks/{taskID}/replay` | `ReplayTask` | owner/admin |
| DELETE | `/v1/conversations/{conversationID}` | `DeleteConversation` | 当前用户会话 |
| GET | `/v1/dead-letters` | `ListDeadLetters` | admin |
| GET | `/v1/admin/tool-policy` | `GetToolPolicy` | admin |
| PUT | `/v1/admin/tool-policy` | `PutToolPolicy` | admin |
| POST | `/v1/admin/tool-policy/reload` | `ReloadToolPolicy` | admin |
| GET | `/v1/admin/tools` | `ListAdminTools` | admin |
| GET | `/v1/memories` | `ListMemories` | 登录，admin 可带 user_id |
| POST | `/v1/memories` | `WriteMemory` | 登录，admin 可写指定 user_id |
| GET | `/v1/memories/recall` | `RecallMemory` | 登录，admin 可带 user_id |
| DELETE | `/v1/memories/{memoryID}` | `DeleteMemory` | 登录，admin 可带 user_id |

## 认证行为

| 接口 | 请求 | 成功响应 |
|---|---|---|
| `POST /v1/auth/register` | `{"username":"devuser","password":"123456"}` | `201 {"user":{"username":"devuser","role":"user"}}` |
| `POST /v1/auth/login` | `{"username":"devuser","password":"123456"}` | `200 {"user":...,"expires_at":...}` 并写 Cookie |
| `POST /v1/auth/logout` | 无 | `200 {"status":"ok"}` 并清 Cookie |
| `GET /v1/auth/me` | Cookie | 当前用户和过期时间 |

规则：

1. 用户名会转小写并去空白。
2. 用户名最短 3 字符，密码最短 6 字符。
3. 登录 Cookie 名为 `synapse_session_token`，HttpOnly，SameSite=Lax，当前 `Secure=false`。
4. Gateway 启动时会 upsert 管理员账号，默认 `admin` / `123456`。

## 任务接口

创建任务：

```json
{
  "prompt": "hello synapse",
  "metadata": {
    "client_view": "chat",
    "conversation_id": "optional-conversation-id",
    "agent_enabled": "true",
    "memory_write_enabled": "true"
  }
}
```

关键行为：

1. `user_id` 来自会话，客户端传入的 `user_id` 不会作为 owner 使用。
2. Gateway 会删除客户端传入的 `model_prompt`、`model_messages_json`、`auth_user_role`、`auth_username`。
3. Gateway 会注入 `auth_user_role`、`auth_username`。
4. 如果 `client_view=chat` 或存在 `conversation_id`，Gateway 会构建会话上下文，并写入 `model_messages_json`。
5. 任务先创建为 `queued`，再入队。

列表查询：

| 参数 | 默认 | 最大 | 说明 |
|---|---|---|---|
| `limit` | 50 | 500 | 返回数量 |
| `status` | 空 | 固定枚举 | queued/running/paused/completed/failed/canceled |

普通用户只能看到自己的任务。管理员可以看到全部任务。

## 取消、审批、重放

| 接口 | 请求字段 | 成功状态码 | 说明 |
|---|---|---|---|
| `POST /v1/tasks/{taskID}/cancel` | `reason` 可选 | 202 或 200 | 首次取消 202，已取消重复请求 200 |
| `POST /v1/tasks/cancel` | `task_ids`、`reason` 可选 | 200 | 支持部分成功，返回 failed 明细 |
| `POST /v1/tasks/{taskID}/approve` | `reason`、`approved_tools`、`approved_tool_call` 可选 | 202 | 仅 paused 可恢复 |
| `POST /v1/tasks/{taskID}/replay` | 无 | 202 | running 不可重放，其它状态可重置 queued |

`approved_tool_call` 示例：

```json
{
  "tool_name": "summarize_page",
  "tool_input": "https://example.com",
  "risk_level": "high",
  "reason": "ops approval",
  "resume_step_index": 1
}
```

## 会话删除

```text
DELETE /v1/conversations/{conversationID}
```

行为：

1. 只删除当前登录用户名下的会话任务。
2. 同时删除任务关联事件和死信记录。
3. 如果被删任务正在运行，会调用 Worker cancel。
4. 兼容历史数据：没有 `conversation_id` 时，可按 task id 作为会话键删除。

响应：

```json
{
  "conversation_id": "conv-a",
  "deleted_count": 2,
  "deleted_task_ids": ["task-1", "task-2"]
}
```

## 长期记忆接口

长期记忆实际存储在 AI Engine 的 FileMemoryStore，Gateway API 通过 gRPC Memory RPC 转发。

| 接口 | 说明 |
|---|---|
| `GET /v1/memories?limit=50` | 列出当前用户近期记忆 |
| `POST /v1/memories` | 手工写入记忆 |
| `GET /v1/memories/recall?query=...&limit=3` | 按 query 召回记忆 |
| `DELETE /v1/memories/{memoryID}` | 删除记忆 |

`POST /v1/memories` 请求：

```json
{
  "content": "Gateway retries should be bounded.",
  "summary": "bounded retries",
  "source_task_id": "task-id",
  "importance": 0.8
}
```

管理员可以通过 `user_id` 查询、写入或删除指定用户记忆；普通用户传入 `user_id` 会被锁定为自己的用户名。

## SSE

```text
GET /v1/tasks/{taskID}/events?last_event_id=0
```

事件 payload：

```json
{
  "event_id": 1,
  "type": "token",
  "message": "",
  "token": "hello",
  "trace_id": "uuid",
  "emitted_at_unix_ms": 1710000000000
}
```

终态任务无新事件后，SSE 会发送：

```json
{"task_id":"...","status":"completed"}
```

事件名为 `terminal`。

## 状态码

| 状态码 | 场景 |
|---|---|
| 200 | 查询成功、幂等取消、批量取消结果、记忆查询 |
| 201 | 注册成功、创建任务成功、写入记忆成功 |
| 202 | 首次取消、审批恢复、重放 |
| 400 | 请求体无效、limit/status/last_event_id 无效 |
| 401 | 未登录或会话失效 |
| 403 | 无权访问资源 |
| 404 | 任务、会话或记忆不存在 |
| 409 | 状态冲突，如终态取消、非 paused 审批、running 重放 |
| 502 | Gateway 调 AI Engine Memory RPC 失败 |
| 503 | 健康检查降级、记忆后端不可用 |

## 待完善

1. 增加 OpenAPI 文档和请求响应 schema。
2. 将错误响应抽象为统一错误码。
3. 为 SSE 增加可选心跳帧。
4. 为任务列表增加分页游标、时间范围和用户维度查询。
