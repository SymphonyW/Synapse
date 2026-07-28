package queue

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/google/uuid"
)

// ErrNoDelivery 表示当前没有可消费的任务投递。
var ErrNoDelivery = errors.New("no task delivery available")

// Delivery 是一次队列投递，携带 Redis Stream 消息 ID 和任务 ID。
type Delivery struct {
	MessageID     string
	TaskID        string
	Consumer      string
	DeliveryCount int64
}

// Stats 暴露队列运行态，用于健康检查和后续指标接入。
type Stats struct {
	Backend          string `json:"backend"`
	Stream           string `json:"stream,omitempty"`
	ConsumerGroup    string `json:"consumer_group,omitempty"`
	ConsumerName     string `json:"consumer_name,omitempty"`
	PendingCount     int64  `json:"pending_count"`
	LastReclaimCount int64  `json:"last_reclaim_count"`
}

// TaskQueue 定义可靠任务投递能力，Redis Streams 和内存队列都实现该接口。
type TaskQueue interface {
	Enqueue(ctx context.Context, taskID string) error
	Claim(ctx context.Context, consumer string) (Delivery, error)
	Ack(ctx context.Context, delivery Delivery) error
	Reclaim(ctx context.Context, consumer string, minIdle time.Duration, limit int) ([]Delivery, error)
	Stats(ctx context.Context) (Stats, error)
	Close() error
}

func NewConsumerName(prefix string) string {
	hostname, err := os.Hostname()
	if err != nil || strings.TrimSpace(hostname) == "" {
		hostname = "host"
	}
	normalizedPrefix := strings.TrimSpace(prefix)
	if normalizedPrefix == "" {
		normalizedPrefix = "gateway"
	}
	return fmt.Sprintf("%s-%s-%d-%s", normalizedPrefix, sanitizeConsumerPart(hostname), os.Getpid(), uuid.NewString()[:8])
}

func sanitizeConsumerPart(value string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	var builder strings.Builder
	for _, r := range value {
		switch {
		case r >= 'a' && r <= 'z':
			builder.WriteRune(r)
		case r >= '0' && r <= '9':
			builder.WriteRune(r)
		case r == '-' || r == '_':
			builder.WriteRune(r)
		default:
			builder.WriteRune('-')
		}
	}
	normalized := strings.Trim(builder.String(), "-_")
	if normalized == "" {
		return "host"
	}
	return normalized
}
