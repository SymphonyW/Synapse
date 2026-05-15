package agent

import (
	"context"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/domain"
	agentv1 "github.com/synapse/synapse/services/gateway-go/internal/gen/synapse/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

// Client 抽象网关对 AI Engine 的调用能力，便于测试替换。
type Client interface {
	// SubmitTask 发起任务并接收流式事件。
	SubmitTask(ctx context.Context, task domain.Task) (agentv1.AgentRuntime_SubmitTaskClient, error)
	// Health 检查 AI Engine 健康状态。
	Health(ctx context.Context) (*agentv1.HealthResponse, error)
	// MemoryWrite 写入长期记忆，实际存储由 AI Engine 的 MemoryStore 负责。
	MemoryWrite(ctx context.Context, request *agentv1.MemoryWriteRequest) (*agentv1.MemoryWriteResponse, error)
	// MemoryRecall 按用户和查询召回长期记忆。
	MemoryRecall(ctx context.Context, request *agentv1.MemoryRecallRequest) (*agentv1.MemoryRecallResponse, error)
	// MemoryDelete 删除指定用户的一条长期记忆。
	MemoryDelete(ctx context.Context, request *agentv1.MemoryDeleteRequest) (*agentv1.MemoryDeleteResponse, error)
	// MemoryList 列出指定用户最近的长期记忆。
	MemoryList(ctx context.Context, request *agentv1.MemoryListRequest) (*agentv1.MemoryListResponse, error)
	GetToolPolicy(ctx context.Context) (*agentv1.GetToolPolicyResponse, error)
	ApplyToolPolicy(ctx context.Context, request *agentv1.ApplyToolPolicyRequest) (*agentv1.ApplyToolPolicyResponse, error)
	ListTools(ctx context.Context) (*agentv1.ListToolsResponse, error)
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

// MemoryWrite 透传长期记忆写入请求。
func (c *GRPCClient) MemoryWrite(ctx context.Context, request *agentv1.MemoryWriteRequest) (*agentv1.MemoryWriteResponse, error) {
	return c.client.MemoryWrite(ctx, request)
}

// MemoryRecall 透传长期记忆召回请求。
func (c *GRPCClient) MemoryRecall(ctx context.Context, request *agentv1.MemoryRecallRequest) (*agentv1.MemoryRecallResponse, error) {
	return c.client.MemoryRecall(ctx, request)
}

// MemoryDelete 透传长期记忆删除请求。
func (c *GRPCClient) MemoryDelete(ctx context.Context, request *agentv1.MemoryDeleteRequest) (*agentv1.MemoryDeleteResponse, error) {
	return c.client.MemoryDelete(ctx, request)
}

// MemoryList 透传长期记忆列表请求。
func (c *GRPCClient) MemoryList(ctx context.Context, request *agentv1.MemoryListRequest) (*agentv1.MemoryListResponse, error) {
	return c.client.MemoryList(ctx, request)
}

func (c *GRPCClient) GetToolPolicy(ctx context.Context) (*agentv1.GetToolPolicyResponse, error) {
	return c.client.GetToolPolicy(ctx, &agentv1.GetToolPolicyRequest{})
}

func (c *GRPCClient) ApplyToolPolicy(ctx context.Context, request *agentv1.ApplyToolPolicyRequest) (*agentv1.ApplyToolPolicyResponse, error) {
	return c.client.ApplyToolPolicy(ctx, request)
}

func (c *GRPCClient) ListTools(ctx context.Context) (*agentv1.ListToolsResponse, error) {
	return c.client.ListTools(ctx, &agentv1.ListToolsRequest{})
}

// Close 关闭 gRPC 连接。
func (c *GRPCClient) Close() error {
	if c.conn == nil {
		return nil
	}
	return c.conn.Close()
}
