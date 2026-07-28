package queue

import (
	"context"
	"errors"
	"strconv"
	"sync"
	"time"
)

// ErrQueueClosed 表示队列已关闭，不能继续入队或消费。
var ErrQueueClosed = errors.New("queue is closed")

type memoryMessage struct {
	delivery  Delivery
	claimedAt time.Time
	acked     bool
}

// InMemoryQueue 使用 channel + pending map 模拟可靠投递，适合本地开发与测试。
type InMemoryQueue struct {
	once             sync.Once
	mu               sync.Mutex
	ch               chan string
	closed           chan struct{}
	pending          map[string]*memoryMessage
	nextID           int64
	lastReclaimCount int64
}

// NewInMemoryQueue 创建内存队列；当 bufferSize 非法时使用默认值。
func NewInMemoryQueue(bufferSize int) *InMemoryQueue {
	if bufferSize <= 0 {
		bufferSize = 1024
	}

	return &InMemoryQueue{
		ch:      make(chan string, bufferSize),
		closed:  make(chan struct{}),
		pending: map[string]*memoryMessage{},
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

// Claim 阻塞获取一个新 delivery，并记录为 pending。
func (q *InMemoryQueue) Claim(ctx context.Context, consumer string) (Delivery, error) {
	select {
	case <-q.closed:
		return Delivery{}, ErrQueueClosed
	case <-ctx.Done():
		return Delivery{}, ctx.Err()
	case taskID := <-q.ch:
		return q.markClaimed(taskID, consumer), nil
	}
}

// Dequeue 保留为测试便利方法；新 Worker 使用 Claim/Ack。
func (q *InMemoryQueue) Dequeue(ctx context.Context) (string, error) {
	delivery, err := q.Claim(ctx, "dequeue")
	if err != nil {
		return "", err
	}
	if err := q.Ack(ctx, delivery); err != nil {
		return "", err
	}
	return delivery.TaskID, nil
}

// Ack 删除 pending delivery。
func (q *InMemoryQueue) Ack(ctx context.Context, delivery Delivery) error {
	select {
	case <-ctx.Done():
		return ctx.Err()
	default:
	}

	q.mu.Lock()
	defer q.mu.Unlock()
	delete(q.pending, delivery.MessageID)
	return nil
}

// Reclaim 将 idle 超过 minIdle 的 pending delivery 转移给 consumer。
func (q *InMemoryQueue) Reclaim(ctx context.Context, consumer string, minIdle time.Duration, limit int) ([]Delivery, error) {
	select {
	case <-q.closed:
		return nil, ErrQueueClosed
	case <-ctx.Done():
		return nil, ctx.Err()
	default:
	}
	if limit <= 0 {
		limit = 1
	}

	now := time.Now().UTC()
	q.mu.Lock()
	defer q.mu.Unlock()

	deliveries := make([]Delivery, 0, limit)
	for _, message := range q.pending {
		if message.acked {
			continue
		}
		if minIdle > 0 && now.Sub(message.claimedAt) < minIdle {
			continue
		}
		message.delivery.Consumer = consumer
		message.delivery.DeliveryCount++
		message.claimedAt = now
		deliveries = append(deliveries, message.delivery)
		if len(deliveries) >= limit {
			break
		}
	}
	q.lastReclaimCount = int64(len(deliveries))
	return deliveries, nil
}

func (q *InMemoryQueue) Stats(context.Context) (Stats, error) {
	q.mu.Lock()
	defer q.mu.Unlock()
	return Stats{
		Backend:          "in-memory",
		PendingCount:     int64(len(q.pending)),
		LastReclaimCount: q.lastReclaimCount,
	}, nil
}

// Close 幂等关闭队列信号。
func (q *InMemoryQueue) Close() error {
	q.once.Do(func() {
		close(q.closed)
	})
	return nil
}

func (q *InMemoryQueue) markClaimed(taskID string, consumer string) Delivery {
	q.mu.Lock()
	defer q.mu.Unlock()

	q.nextID++
	delivery := Delivery{
		MessageID:     strconv.FormatInt(q.nextID, 10),
		TaskID:        taskID,
		Consumer:      consumer,
		DeliveryCount: 1,
	}
	q.pending[delivery.MessageID] = &memoryMessage{
		delivery:  delivery,
		claimedAt: time.Now().UTC(),
	}
	return delivery
}
