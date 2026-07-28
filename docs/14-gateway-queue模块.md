# Gateway Queue 模块

## 1. 模块定位

Queue 模块负责任务 ID 的可靠投递与消费抽象，屏蔽内存队列和 Redis Streams 后端差异。

文件：

1. [services/gateway-go/internal/queue/queue.go](../services/gateway-go/internal/queue/queue.go)
2. [services/gateway-go/internal/queue/inmemory.go](../services/gateway-go/internal/queue/inmemory.go)
3. [services/gateway-go/internal/queue/redis.go](../services/gateway-go/internal/queue/redis.go)

## 2. 抽象接口

TaskQueue 提供：

1. `Enqueue(ctx, taskID)`
2. `Claim(ctx, consumer) -> Delivery`
3. `Ack(ctx, delivery)`
4. `Reclaim(ctx, consumer, minIdle, limit)`
5. `Stats(ctx)`
6. `Close()`

`Delivery` 包含 `message_id`、`task_id`、`consumer` 和 `delivery_count`。Worker 必须在终态、暂停或死信后 Ack。

## 3. InMemoryQueue

1. 基于 channel + pending map。
2. Claim 后进入 pending，Ack 后删除。
3. Reclaim 可转移 idle pending delivery，但不提供跨进程持久化。

## 4. RedisQueue

1. 入队：`XADD`。
2. 领取：`XREADGROUP`。
3. 确认：`XACK`。
4. 恢复：`XAUTOCLAIM`。
5. 启动时创建 consumer group，`BUSYGROUP` 视为正常。

## 5. 运行时选择

Gateway 启动时：

1. 如果 `SYNAPSE_REDIS_ADDR` 可用且连接成功，使用 Redis Streams。
2. 否则回退 InMemoryQueue。

## 6. 语义

1. Redis Streams 是至少一次投递。
2. Worker 崩溃后，未 Ack 的 pending message 由其他 consumer reclaim。
3. 数据库 execution lease 保证同一任务不会被多个实例同时执行。
4. `paused/completed/failed/canceled` 的重复投递会被 Ack 且不重新执行。

## 7. 运维关注

1. `/healthz` 输出 `task_queue.pending_count`、`consumer_group`、`consumer_name`。
2. `SYNAPSE_TASK_VISIBILITY_TIMEOUT` 应大于正常任务执行时间。
3. 从旧 List 升级时，停止旧 Worker 并人工处理旧 List 中的遗留任务。
