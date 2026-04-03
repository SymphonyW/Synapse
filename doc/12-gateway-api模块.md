# Gateway API 模块

## 1. 模块定位

API 模块负责对外 HTTP 接口实现，包括认证、任务管理、SSE 事件流与运维接口。

核心文件：

1. [services/gateway-go/internal/api/router.go](../services/gateway-go/internal/api/router.go)
2. [services/gateway-go/internal/api/handlers.go](../services/gateway-go/internal/api/handlers.go)
3. [services/gateway-go/internal/api/handlers_auth.go](../services/gateway-go/internal/api/handlers_auth.go)

## 2. 路由清单

1. GET /healthz
2. POST /v1/auth/register
3. POST /v1/auth/login
4. POST /v1/auth/logout
5. GET /v1/auth/me
6. GET /v1/tasks
7. POST /v1/tasks
8. GET /v1/tasks/{taskID}
9. POST /v1/tasks/{taskID}/cancel
10. POST /v1/tasks/cancel
11. POST /v1/tasks/{taskID}/replay
12. GET /v1/tasks/{taskID}/events
13. GET /v1/dead-letters

## 3. 认证相关行为

1. 注册创建普通用户，密码使用 bcrypt 哈希。
2. 登录后创建会话并写入 HttpOnly Cookie。
3. /v1/auth/me 返回当前会话身份。
4. 注销时删除会话并清理 Cookie。

## 4. 任务相关行为

1. CreateTask：校验身份，创建 queued 任务，入队。
2. GetTask/ListTasks：按身份过滤可见范围。
3. CancelTask：支持首次取消与幂等取消。
4. BatchCancel：支持部分成功并返回失败明细。
5. ReplayTask：非 running 任务可重置并重新入队。

## 5. SSE 事件流

1. 接口：GET /v1/tasks/{taskID}/events
2. 支持参数：last_event_id
3. 终态任务在无新事件后发送 terminal 并关闭。
4. 典型事件：started/token/completed/failed/cancel_requested/canceled。

## 6. 返回码约定（关键）

1. 401：未认证或会话失效
2. 403：无权限访问资源
3. 404：任务不存在
4. 409：终态任务不可取消/不可重放
5. 202：首次取消成功（异步语义）
6. 200：幂等取消（已是 canceled）

## 7. 可维护性建议

1. 建议抽离统一错误码与错误响应结构。
2. 建议为每个接口补充 OpenAPI 文档与请求示例。
3. 建议在 SSE 输出中增加可选心跳帧，方便前端保活。
