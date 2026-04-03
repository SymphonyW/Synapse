package agent

import (
	"context"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/domain"
	agentv1 "github.com/synapse/synapse/services/gateway-go/internal/gen/synapse/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

// Client 抽象网关对 AI 引擎的调用能力，便于测试替换。
type Client interface {
	// SubmitTask 发起任务并接收流式事件。
	SubmitTask(ctx context.Context, task domain.Task) (agentv1.AgentRuntime_SubmitTaskClient, error)
	// Health 检查 AI 引擎健康状态。
	Health(ctx context.Context) (*agentv1.HealthResponse, error)
	// Close 释放底层连接资源。
	Close() error
}

// GRPCClient 是 Client 的 gRPC 实现。
type GRPCClient struct {
	conn   *grpc.ClientConn
	client agentv1.AgentRuntimeClient
}

// NewGRPCClient 创建阻塞式 gRPC 连接，连接超时为 5 秒。
func NewGRPCClient(addr string) (*GRPCClient, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	conn, err := grpc.DialContext(ctx, addr, grpc.WithTransportCredentials(insecure.NewCredentials()), grpc.WithBlock())
	if err != nil {
		return nil, err
	}

	return &GRPCClient{
		conn:   conn,
		client: agentv1.NewAgentRuntimeClient(conn),
	}, nil
}

// SubmitTask 将领域任务映射为 proto 请求并调用远端服务。
func (c *GRPCClient) SubmitTask(ctx context.Context, task domain.Task) (agentv1.AgentRuntime_SubmitTaskClient, error) {
	request := &agentv1.SubmitTaskRequest{
		TaskId:   task.ID,
		UserId:   task.UserID,
		Prompt:   task.Prompt,
		Metadata: task.Metadata,
	}

	return c.client.SubmitTask(ctx, request)
}

// Health 透传健康检查调用。
func (c *GRPCClient) Health(ctx context.Context) (*agentv1.HealthResponse, error) {
	return c.client.Health(ctx, &agentv1.HealthRequest{})
}

// Close 关闭 gRPC 连接。
func (c *GRPCClient) Close() error {
	if c.conn == nil {
		return nil
	}
	return c.conn.Close()
}
