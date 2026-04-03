# Gateway Worker 模块

## 1. 模块定位

Worker 模块负责消费任务队列，调用 AI Engine 执行任务，并完成状态迁移、重试、取消、死信处理。

文件： [services/gateway-go/internal/worker/processor.go](../services/gateway-go/internal/worker/processor.go)

## 2. 主要结构

TaskProcessor 依赖：

1. taskStore
2. taskQueue
3. agent client（gRPC）
4. ProcessorOptions（ExecutionTimeout, MaxAttempts, RetryBackoff）

## 3. 执行流程

1. Run 循环阻塞 Dequeue。
2. processWithRetry 控制重试生命周期。
3. processTask 内部调用 agent.SubmitTask，持续 Recv 事件。
4. 每条事件落库，必要时驱动状态更新。
5. 终态后清理活跃任务上下文。

## 4. 重试策略

1. 可配置最大尝试次数与回退时间。
2. 不可重试错误快速失败（如 DeadlineExceeded、InvalidArgument、PermissionDenied）。
3. 可重试错误在重试前写入 info 事件，便于前端和运维观测。

## 5. 取消语义

1. API 层更新任务状态为 canceled，并调用 TaskProcessor.Cancel(taskID)。
2. Worker 通过 active map 找到 cancel func，终止当前执行上下文。
3. finalizeCanceled 保留已有取消原因，并写入 canceled 事件。

## 6. 死信语义

1. 达到最大重试仍失败 -> finalizeFailed。
2. 更新任务状态 failed。
3. 记录 dead_letter_tasks。
4. 写入 failed + dead_lettered 事件。

## 7. 扩展建议

1. 目前 Run 为串行消费，可扩展为可控并发 worker pool。
2. 增加任务级超时、优先级、隔离队列等能力。
3. 建议引入链路追踪，贯穿 task_id 与 trace_id。
