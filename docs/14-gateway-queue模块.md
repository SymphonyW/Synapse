# Gateway Queue 模块

## 1. 模块定位

Queue 模块负责任务 ID 的投递与消费抽象，屏蔽队列后端差异。

文件：

1. [services/gateway-go/internal/queue/queue.go](../services/gateway-go/internal/queue/queue.go)
2. [services/gateway-go/internal/queue/inmemory.go](../services/gateway-go/internal/queue/inmemory.go)
3. [services/gateway-go/internal/queue/redis.go](../services/gateway-go/internal/queue/redis.go)

## 2. 抽象接口

TaskQueue 提供三个能力：

1. Enqueue(ctx, taskID)
2. Dequeue(ctx)
3. Close()

上层（API/Worker）不依赖具体实现。

## 3. InMemoryQueue 实现

1. 基于带缓冲 channel。
2. 适合本地开发、单进程测试。
3. 支持关闭信号与上下文取消。
4. 不支持跨进程共享与持久化。

## 4. RedisQueue 实现

1. 入队：LPush。
2. 出队：BRPop（短超时轮询）。
3. 适合容器化多实例场景。
4. 依赖 Redis 可用性与网络稳定性。

## 5. 运行时选择策略

Gateway 启动时：

1. 如果 SYNAPSE_REDIS_ADDR 可用且连接成功，使用 RedisQueue。
2. 否则回退 InMemoryQueue。

## 6. 语义与限制

1. 当前实现是“至少尝试执行”语义，没有显式 ack/reclaim。
2. 若 Worker 在执行中崩溃，任务可能需要依赖上层状态修复。
3. 对高可靠场景建议升级到支持消费确认的消息系统。

## 7. 优化方向

1. 引入可观测指标：队列长度、出队延迟。
2. 增加幂等键策略，避免重复入队异常放大。
3. 若要更强语义，可迁移到 Redis Stream、NATS JetStream 或 Kafka。
