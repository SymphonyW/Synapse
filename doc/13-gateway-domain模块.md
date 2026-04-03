# Gateway Domain 模块

## 1. 模块定位

Domain 模块定义系统核心领域模型，为 API、Store、Worker 提供统一数据结构。

文件：

1. [services/gateway-go/internal/domain/task.go](../services/gateway-go/internal/domain/task.go)
2. [services/gateway-go/internal/domain/auth.go](../services/gateway-go/internal/domain/auth.go)

## 2. 任务领域模型

Task：

1. id
2. user_id
3. prompt
4. status
5. error
6. metadata
7. created_at
8. updated_at

TaskStatus：

1. queued
2. running
3. paused
4. completed
5. failed
6. canceled

TaskEvent：

1. id（递增事件游标）
2. task_id
3. type
4. message
5. token
6. trace_id
7. emitted_at_unix_ms
8. created_at

DeadLetterTask：

1. task_id
2. reason
3. attempts
4. created_at
5. updated_at

## 3. 认证领域模型

UserRole：

1. admin
2. user

AuthUser：

1. username
2. password_hash
3. role
4. created_at
5. updated_at

AuthSession：

1. token
2. username
3. role
4. expires_at
5. created_at

## 4. 设计价值

1. 类型边界清晰，避免不同层各自定义结构体。
2. Store/Queue/Worker 都围绕同一模型协作。
3. 便于后续生成 API 文档与事件契约文档。

## 5. 后续建议

1. 为 Task.Metadata 增加显式 schema 或 typed metadata。
2. 为 TaskEvent.Type 建议转为枚举常量，减少字符串拼写风险。
