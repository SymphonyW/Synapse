package queue

import "context"

// TaskQueue 定义任务投递与消费的最小能力，方便在内存队列与 Redis 队列之间切换。
type TaskQueue interface {
	// Enqueue 将任务 ID 放入队列，供工作线程异步处理。
	Enqueue(ctx context.Context, taskID string) error
	// Dequeue 阻塞获取下一个任务 ID；由上层上下文控制退出时机。
	Dequeue(ctx context.Context) (string, error)
	// Close 释放队列资源。
	Close() error
}
