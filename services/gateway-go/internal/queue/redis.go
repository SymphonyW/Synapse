package queue

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync/atomic"
	"time"

	"github.com/redis/go-redis/v9"
)

const deliveryTaskIDField = "task_id"

type RedisOptions struct {
	Addr          string
	Password      string
	DB            int
	Stream        string
	ConsumerGroup string
	ConsumerName  string
	StreamMaxLen  int64
	BlockTimeout  time.Duration
}

type RedisQueue struct {
	client        *redis.Client
	stream        string
	consumerGroup string
	consumerName  string
	streamMaxLen  int64
	blockTimeout  time.Duration
	lastReclaim   atomic.Int64
}

// NewRedisQueue 初始化 Redis Streams consumer group 并验证连通性。
func NewRedisQueue(ctx context.Context, opts RedisOptions) (*RedisQueue, error) {
	if strings.TrimSpace(opts.Stream) == "" {
		opts.Stream = "synapse:tasks"
	}
	if strings.TrimSpace(opts.ConsumerGroup) == "" {
		opts.ConsumerGroup = "synapse-gateway"
	}
	if strings.TrimSpace(opts.ConsumerName) == "" {
		opts.ConsumerName = NewConsumerName("gateway")
	}
	if opts.BlockTimeout <= 0 {
		opts.BlockTimeout = time.Second
	}
	if opts.StreamMaxLen < 0 {
		opts.StreamMaxLen = 0
	}

	client := redis.NewClient(&redis.Options{
		Addr:     opts.Addr,
		Password: opts.Password,
		DB:       opts.DB,
	})

	if err := client.Ping(ctx).Err(); err != nil {
		_ = client.Close()
		return nil, err
	}

	queue := &RedisQueue{
		client:        client,
		stream:        opts.Stream,
		consumerGroup: opts.ConsumerGroup,
		consumerName:  opts.ConsumerName,
		streamMaxLen:  opts.StreamMaxLen,
		blockTimeout:  opts.BlockTimeout,
	}

	if err := queue.ensureGroup(ctx); err != nil {
		_ = client.Close()
		return nil, err
	}
	return queue, nil
}

// Enqueue 使用 XADD 写入 Stream，保留 Redis 生成的消息 ID 供消费端 ack。
func (q *RedisQueue) Enqueue(ctx context.Context, taskID string) error {
	args := &redis.XAddArgs{
		Stream: q.stream,
		Values: map[string]any{deliveryTaskIDField: taskID},
	}
	if q.streamMaxLen > 0 {
		args.MaxLen = q.streamMaxLen
		args.Approx = true
	}
	return q.client.XAdd(ctx, args).Err()
}

// Claim 使用 XREADGROUP 阻塞读取新消息。空读返回 ErrNoDelivery。
func (q *RedisQueue) Claim(ctx context.Context, consumer string) (Delivery, error) {
	consumer = q.consumerOrDefault(consumer)
	result, err := q.client.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group:    q.consumerGroup,
		Consumer: consumer,
		Streams:  []string{q.stream, ">"},
		Count:    1,
		Block:    q.blockTimeout,
	}).Result()
	if errors.Is(err, redis.Nil) {
		return Delivery{}, ErrNoDelivery
	}
	if err != nil {
		return Delivery{}, err
	}

	deliveries, err := q.deliveriesFromStreams(ctx, result, consumer)
	if err != nil {
		return Delivery{}, err
	}
	if len(deliveries) == 0 {
		return Delivery{}, ErrNoDelivery
	}
	return deliveries[0], nil
}

// Ack 使用 XACK 显式确认消息，删除 pending 记录。
func (q *RedisQueue) Ack(ctx context.Context, delivery Delivery) error {
	messageID := strings.TrimSpace(delivery.MessageID)
	if messageID == "" {
		return nil
	}
	return q.client.XAck(ctx, q.stream, q.consumerGroup, messageID).Err()
}

// Reclaim 使用 XAUTOCLAIM 领取 idle 超过 minIdle 的 pending 消息。
func (q *RedisQueue) Reclaim(ctx context.Context, consumer string, minIdle time.Duration, limit int) ([]Delivery, error) {
	if limit <= 0 {
		limit = 1
	}
	consumer = q.consumerOrDefault(consumer)
	messages, _, err := q.client.XAutoClaim(ctx, &redis.XAutoClaimArgs{
		Stream:   q.stream,
		Group:    q.consumerGroup,
		Consumer: consumer,
		MinIdle:  minIdle,
		Start:    "0-0",
		Count:    int64(limit),
	}).Result()
	if errors.Is(err, redis.Nil) {
		q.lastReclaim.Store(0)
		return []Delivery{}, nil
	}
	if err != nil {
		return nil, err
	}

	deliveries, err := q.deliveriesFromMessages(ctx, messages, consumer)
	if err != nil {
		return nil, err
	}
	q.lastReclaim.Store(int64(len(deliveries)))
	return deliveries, nil
}

func (q *RedisQueue) Stats(ctx context.Context) (Stats, error) {
	pendingCount := int64(0)
	pending, err := q.client.XPending(ctx, q.stream, q.consumerGroup).Result()
	if err != nil && !errors.Is(err, redis.Nil) {
		return Stats{}, err
	}
	if pending != nil {
		pendingCount = pending.Count
	}
	return Stats{
		Backend:          "redis-stream",
		Stream:           q.stream,
		ConsumerGroup:    q.consumerGroup,
		ConsumerName:     q.consumerName,
		PendingCount:     pendingCount,
		LastReclaimCount: q.lastReclaim.Load(),
	}, nil
}

// Close 关闭 Redis 客户端连接。
func (q *RedisQueue) Close() error {
	if q.client == nil {
		return nil
	}
	return q.client.Close()
}

func (q *RedisQueue) ensureGroup(ctx context.Context) error {
	err := q.client.XGroupCreateMkStream(ctx, q.stream, q.consumerGroup, "0").Err()
	if err != nil && !isBusyGroupError(err) {
		return err
	}
	if err := q.client.XGroupCreateConsumer(ctx, q.stream, q.consumerGroup, q.consumerName).Err(); err != nil {
		return err
	}
	return nil
}

func (q *RedisQueue) deliveriesFromStreams(ctx context.Context, streams []redis.XStream, consumer string) ([]Delivery, error) {
	deliveries := make([]Delivery, 0)
	for _, stream := range streams {
		if stream.Stream != q.stream {
			continue
		}
		converted, err := q.deliveriesFromMessages(ctx, stream.Messages, consumer)
		if err != nil {
			return nil, err
		}
		deliveries = append(deliveries, converted...)
	}
	return deliveries, nil
}

func (q *RedisQueue) deliveriesFromMessages(ctx context.Context, messages []redis.XMessage, consumer string) ([]Delivery, error) {
	deliveries := make([]Delivery, 0, len(messages))
	for _, message := range messages {
		taskID := strings.TrimSpace(fmt.Sprint(message.Values[deliveryTaskIDField]))
		delivery := Delivery{
			MessageID:     message.ID,
			TaskID:        taskID,
			Consumer:      consumer,
			DeliveryCount: q.deliveryCount(ctx, message.ID),
		}
		if delivery.DeliveryCount <= 0 {
			delivery.DeliveryCount = 1
		}
		deliveries = append(deliveries, delivery)
	}
	return deliveries, nil
}

func (q *RedisQueue) deliveryCount(ctx context.Context, messageID string) int64 {
	entries, err := q.client.XPendingExt(ctx, &redis.XPendingExtArgs{
		Stream: q.stream,
		Group:  q.consumerGroup,
		Start:  messageID,
		End:    messageID,
		Count:  1,
	}).Result()
	if err != nil || len(entries) == 0 {
		return 1
	}
	return entries[0].RetryCount
}

func (q *RedisQueue) consumerOrDefault(consumer string) string {
	trimmed := strings.TrimSpace(consumer)
	if trimmed != "" {
		return trimmed
	}
	return q.consumerName
}

func isBusyGroupError(err error) bool {
	return err != nil && strings.Contains(strings.ToUpper(err.Error()), "BUSYGROUP")
}
