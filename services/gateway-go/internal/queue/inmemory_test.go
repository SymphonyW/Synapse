package queue

import (
	"context"
	"testing"
	"time"
)

func TestInMemoryQueueClaimAckAndReclaim(t *testing.T) {
	taskQueue := NewInMemoryQueue(4)
	ctx := context.Background()

	if err := taskQueue.Enqueue(ctx, "task-1"); err != nil {
		t.Fatalf("Enqueue returned error: %v", err)
	}

	delivery, err := taskQueue.Claim(ctx, "worker-a")
	if err != nil {
		t.Fatalf("Claim returned error: %v", err)
	}
	if delivery.TaskID != "task-1" || delivery.Consumer != "worker-a" || delivery.DeliveryCount != 1 {
		t.Fatalf("unexpected delivery: %#v", delivery)
	}

	stats, err := taskQueue.Stats(ctx)
	if err != nil {
		t.Fatalf("Stats returned error: %v", err)
	}
	if stats.PendingCount != 1 {
		t.Fatalf("unexpected pending count after claim: %d", stats.PendingCount)
	}

	reclaimed, err := taskQueue.Reclaim(ctx, "worker-b", 0, 10)
	if err != nil {
		t.Fatalf("Reclaim returned error: %v", err)
	}
	if len(reclaimed) != 1 {
		t.Fatalf("unexpected reclaim count: %d", len(reclaimed))
	}
	if reclaimed[0].Consumer != "worker-b" || reclaimed[0].DeliveryCount != 2 {
		t.Fatalf("unexpected reclaimed delivery: %#v", reclaimed[0])
	}

	if err := taskQueue.Ack(ctx, reclaimed[0]); err != nil {
		t.Fatalf("Ack returned error: %v", err)
	}
	stats, err = taskQueue.Stats(ctx)
	if err != nil {
		t.Fatalf("Stats returned error: %v", err)
	}
	if stats.PendingCount != 0 {
		t.Fatalf("unexpected pending count after ack: %d", stats.PendingCount)
	}

	reclaimed, err = taskQueue.Reclaim(ctx, "worker-c", time.Nanosecond, 10)
	if err != nil {
		t.Fatalf("Reclaim returned error: %v", err)
	}
	if len(reclaimed) != 0 {
		t.Fatalf("acked delivery should not be reclaimed: %#v", reclaimed)
	}
}
