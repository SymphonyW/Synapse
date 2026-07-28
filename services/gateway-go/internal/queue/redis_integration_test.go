package queue

import (
	"context"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

func TestRedisStreamQueueClaimAckAndReclaim(t *testing.T) {
	addr := strings.TrimSpace(os.Getenv("SYNAPSE_TEST_REDIS_ADDR"))
	if addr == "" {
		t.Skip("SYNAPSE_TEST_REDIS_ADDR is not set")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	stream := "synapse:test:tasks:" + uuid.NewString()
	group := "test-group"
	client := redis.NewClient(&redis.Options{Addr: addr})
	t.Cleanup(func() {
		_ = client.Del(context.Background(), stream).Err()
		_ = client.Close()
	})

	taskQueue, err := NewRedisQueue(ctx, RedisOptions{
		Addr:          addr,
		Stream:        stream,
		ConsumerGroup: group,
		ConsumerName:  "worker-a",
		BlockTimeout:  10 * time.Millisecond,
	})
	if err != nil {
		t.Fatalf("NewRedisQueue returned error: %v", err)
	}
	defer taskQueue.Close()

	if err := taskQueue.Enqueue(ctx, "task-1"); err != nil {
		t.Fatalf("Enqueue returned error: %v", err)
	}
	delivery, err := taskQueue.Claim(ctx, "worker-a")
	if err != nil {
		t.Fatalf("Claim returned error: %v", err)
	}
	if delivery.TaskID != "task-1" || delivery.MessageID == "" {
		t.Fatalf("unexpected delivery: %#v", delivery)
	}
	stats, err := taskQueue.Stats(ctx)
	if err != nil {
		t.Fatalf("Stats returned error: %v", err)
	}
	if stats.PendingCount != 1 {
		t.Fatalf("unexpected pending count after claim: %d", stats.PendingCount)
	}
	if err := taskQueue.Ack(ctx, delivery); err != nil {
		t.Fatalf("Ack returned error: %v", err)
	}
	stats, err = taskQueue.Stats(ctx)
	if err != nil {
		t.Fatalf("Stats returned error: %v", err)
	}
	if stats.PendingCount != 0 {
		t.Fatalf("unexpected pending count after ack: %d", stats.PendingCount)
	}

	if err := taskQueue.Enqueue(ctx, "task-2"); err != nil {
		t.Fatalf("Enqueue returned error: %v", err)
	}
	delivery, err = taskQueue.Claim(ctx, "worker-a")
	if err != nil {
		t.Fatalf("Claim returned error: %v", err)
	}
	reclaimed, err := taskQueue.Reclaim(ctx, "worker-b", 0, 10)
	if err != nil {
		t.Fatalf("Reclaim returned error: %v", err)
	}
	if len(reclaimed) != 1 {
		t.Fatalf("unexpected reclaim count: %d", len(reclaimed))
	}
	if reclaimed[0].MessageID != delivery.MessageID || reclaimed[0].Consumer != "worker-b" {
		t.Fatalf("unexpected reclaimed delivery: %#v", reclaimed[0])
	}
	if err := taskQueue.Ack(ctx, reclaimed[0]); err != nil {
		t.Fatalf("Ack reclaimed returned error: %v", err)
	}
}

func TestRedisStreamQueueClosedClientReturnsError(t *testing.T) {
	addr := strings.TrimSpace(os.Getenv("SYNAPSE_TEST_REDIS_ADDR"))
	if addr == "" {
		t.Skip("SYNAPSE_TEST_REDIS_ADDR is not set")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	stream := "synapse:test:tasks:" + uuid.NewString()
	taskQueue, err := NewRedisQueue(ctx, RedisOptions{
		Addr:          addr,
		Stream:        stream,
		ConsumerGroup: "test-group",
		ConsumerName:  "worker-a",
		BlockTimeout:  10 * time.Millisecond,
	})
	if err != nil {
		t.Fatalf("NewRedisQueue returned error: %v", err)
	}
	_ = taskQueue.client.Del(ctx, stream).Err()
	if err := taskQueue.Close(); err != nil {
		t.Fatalf("Close returned error: %v", err)
	}

	if _, err := taskQueue.Claim(ctx, "worker-a"); err == nil {
		t.Fatal("expected closed redis client to return error")
	}
}
