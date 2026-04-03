package queue

import (
	"context"
	"errors"
	"sync"
)

// ErrQueueClosed 表示队列已关闭，不能继续入队或出队。
var ErrQueueClosed = errors.New("queue is closed")

// InMemoryQueue 使用缓冲 channel 模拟队列，适合本地开发与测试。
type InMemoryQueue struct {
	once   sync.Once
	ch     chan string
	closed chan struct{}
}

// NewInMemoryQueue 创建内存队列；当 bufferSize 非法时使用默认值。
func NewInMemoryQueue(bufferSize int) *InMemoryQueue {
	if bufferSize <= 0 {
		bufferSize = 1024
	}

	return &InMemoryQueue{
		ch:     make(chan string, bufferSize),
		closed: make(chan struct{}),
	}
}

// Enqueue 尝试入队，关闭与上下文取消会立即返回对应错误。
func (q *InMemoryQueue) Enqueue(ctx context.Context, taskID string) error {
	select {
	case <-q.closed:
		return ErrQueueClosed
	case <-ctx.Done():
		return ctx.Err()
	case q.ch <- taskID:
		return nil
	}
}

// Dequeue 阻塞读取一个任务 ID。
func (q *InMemoryQueue) Dequeue(ctx context.Context) (string, error) {
	select {
	case <-q.closed:
		return "", ErrQueueClosed
	case <-ctx.Done():
		return "", ctx.Err()
	case taskID := <-q.ch:
		return taskID, nil
	}
}

// Close 幂等关闭队列信号。
func (q *InMemoryQueue) Close() error {
	q.once.Do(func() {
		close(q.closed)
	})
	return nil
}
