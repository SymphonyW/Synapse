package queue

import (
	"context"
	"errors"
	"time"

	"github.com/redis/go-redis/v9"
)

type RedisQueue struct {
	client   *redis.Client
	listName string
}

// NewRedisQueue 初始化 Redis 客户端并验证连通性。
func NewRedisQueue(ctx context.Context, addr string, password string, db int, listName string) (*RedisQueue, error) {
	client := redis.NewClient(&redis.Options{
		Addr:     addr,
		Password: password,
		DB:       db,
	})

	if err := client.Ping(ctx).Err(); err != nil {
		_ = client.Close()
		return nil, err
	}

	if listName == "" {
		listName = "synapse:tasks"
	}

	return &RedisQueue{client: client, listName: listName}, nil
}

// Enqueue 使用 LPush 入队。
func (q *RedisQueue) Enqueue(ctx context.Context, taskID string) error {
	return q.client.LPush(ctx, q.listName, taskID).Err()
}

// Dequeue 使用 BRPop 轮询出队，空队列会继续等待。
func (q *RedisQueue) Dequeue(ctx context.Context) (string, error) {
	for {
		result, err := q.client.BRPop(ctx, time.Second, q.listName).Result()
		if err == nil {
			if len(result) != 2 {
				continue
			}
			return result[1], nil
		}

		if errors.Is(err, redis.Nil) {
			continue
		}
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			return "", err
		}

		// Redis 瞬时错误交给上层处理重试与降级。
		return "", err
	}
}

// Close 关闭 Redis 客户端连接。
func (q *RedisQueue) Close() error {
	if q.client == nil {
		return nil
	}
	return q.client.Close()
}
