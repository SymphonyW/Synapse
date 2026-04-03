# Gateway Store 模块

## 1. 模块定位

Store 模块负责任务、事件、死信、认证用户、会话的持久化抽象。

文件：

1. [services/gateway-go/internal/store/store.go](../services/gateway-go/internal/store/store.go)
2. [services/gateway-go/internal/store/inmemory.go](../services/gateway-go/internal/store/inmemory.go)
3. [services/gateway-go/internal/store/postgres.go](../services/gateway-go/internal/store/postgres.go)

## 2. 抽象能力

TaskStore 接口覆盖：

1. 任务 CRUD 与状态更新
2. 任务 metadata 合并更新（UpdateMetadata）
3. 事件追加与增量读取
4. 死信记录与查询
5. 用户与会话管理

该抽象使 API 与 Worker 可以独立于存储实现开发和测试。

## 3. InMemoryStore 特点

1. map + mutex 实现，线程安全。
2. 适合测试与开发回退。
3. 事件 ID 在进程内递增。
4. 重启后数据丢失。

## 4. PostgresStore 特点

1. 启动自动 ensure schema。
2. metadata 使用 JSONB，便于扩展。
3. task_events 使用 (task_id, id) 索引支撑 SSE 增量读取。
4. auth_sessions 具备过期索引便于清理。
5. UpdateMetadata 采用“先读取再合并再写回”的方式，空值会删除对应 key。

## 5. 数据表概览

1. tasks：任务主记录。
2. task_events：任务事件日志。
3. dead_letter_tasks：死信任务。
4. auth_users：认证用户。
5. auth_sessions：登录会话。

## 6. 关键语义

1. ListEvents 在任务不存在时返回 ErrTaskNotFound，便于 SSE 层区分“无新事件”与“任务不存在”。
2. MarkDeadLetter 采用 UPSERT，记录最新失败原因和尝试次数。
3. 会话查询只返回未过期会话。
4. UpdateMetadata 对 key 做 trim，空 key 忽略，空 value 表示删除；用于审批恢复元数据写入。

## 7. 生产建议

1. 使用 migration 工具替代启动自动建表。
2. 对任务列表接口增加分页和复合索引优化。
3. 增加数据库连接池参数配置项与指标导出。
