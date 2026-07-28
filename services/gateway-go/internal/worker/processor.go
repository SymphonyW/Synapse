package worker

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/synapse/synapse/services/gateway-go/internal/agent"
	"github.com/synapse/synapse/services/gateway-go/internal/agentinfo"
	"github.com/synapse/synapse/services/gateway-go/internal/domain"
	"github.com/synapse/synapse/services/gateway-go/internal/queue"
	"github.com/synapse/synapse/services/gateway-go/internal/store"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

// ErrTaskCanceled 是内部哨兵错误，用于在“主动取消”场景下短路重试逻辑。
var ErrTaskCanceled = errors.New("task canceled")

const metadataModelPromptKey = "model_prompt"
const metadataAgentEnabledKey = "agent_enabled"
const metadataApprovalGrantedKey = "approval_granted"
const metadataAgentResumeStepKey = "agent_resume_step_index"
const metadataAgentRequiredToolKey = "agent_required_tool"
const metadataAgentRequiredToolInputKey = "agent_required_tool_input"
const metadataAgentRequiredToolRiskKey = "agent_required_tool_risk_level"
const metadataAgentRequiredReasonKey = "agent_required_reason"

// ProcessorOptions 控制任务执行循环的运行时行为。
type ProcessorOptions struct {
	ExecutionTimeout  time.Duration
	MaxAttempts       int
	RetryBackoff      time.Duration
	ConsumerName      string
	VisibilityTimeout time.Duration
	ReclaimInterval   time.Duration
	ReclaimBatchSize  int
}

type activeExecution struct {
	cancel    context.CancelFunc
	messageID string
}

// TaskProcessor 负责消费队列任务、调用 AI Runtime、持久化事件并执行重试/取消语义。
type TaskProcessor struct {
	taskStore store.TaskStore
	taskQueue queue.TaskQueue
	agent     agent.Client
	options   ProcessorOptions
	activeMu  sync.Mutex
	active    map[string]activeExecution
}

// NewTaskProcessor 填充合理默认值，调用方可按需覆盖。
func NewTaskProcessor(taskStore store.TaskStore, taskQueue queue.TaskQueue, agentClient agent.Client, options ProcessorOptions) *TaskProcessor {
	if options.ExecutionTimeout <= 0 {
		options.ExecutionTimeout = 120 * time.Second
	}
	if options.MaxAttempts <= 0 {
		options.MaxAttempts = 3
	}
	if options.RetryBackoff <= 0 {
		options.RetryBackoff = 2 * time.Second
	}
	if options.ConsumerName == "" {
		options.ConsumerName = queue.NewConsumerName("worker")
	}
	if options.VisibilityTimeout <= 0 {
		options.VisibilityTimeout = options.ExecutionTimeout + 30*time.Second
	}
	if options.ReclaimInterval <= 0 {
		options.ReclaimInterval = 10 * time.Second
	}
	if options.ReclaimBatchSize <= 0 {
		options.ReclaimBatchSize = 10
	}

	return &TaskProcessor{
		taskStore: taskStore,
		taskQueue: taskQueue,
		agent:     agentClient,
		options:   options,
		active:    map[string]activeExecution{},
	}
}

// Cancel 通过调用已登记的取消函数，请求终止正在执行的任务。
func (p *TaskProcessor) Cancel(taskID string) bool {
	p.activeMu.Lock()
	active, exists := p.active[taskID]
	p.activeMu.Unlock()
	if !exists {
		return false
	}

	active.cancel()
	return true
}

// Run 是主消费循环：Claim delivery，处理完成后显式 Ack，崩溃时由 reclaim 恢复。
func (p *TaskProcessor) Run(ctx context.Context) {
	reclaimed := make(chan queue.Delivery, p.options.ReclaimBatchSize)
	go p.reclaimLoop(ctx, reclaimed)

	for {
		select {
		case <-ctx.Done():
			return
		case delivery := <-reclaimed:
			p.handleDelivery(ctx, delivery)
			continue
		default:
		}

		delivery, err := p.taskQueue.Claim(ctx, p.options.ConsumerName)
		if err != nil {
			if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
				return
			}
			if errors.Is(err, queue.ErrNoDelivery) {
				continue
			}
			log.Printf("task claim error: %v", err)
			time.Sleep(500 * time.Millisecond)
			continue
		}

		p.handleDelivery(ctx, delivery)
	}
}

func (p *TaskProcessor) reclaimLoop(ctx context.Context, out chan<- queue.Delivery) {
	ticker := time.NewTicker(p.options.ReclaimInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			deliveries, err := p.taskQueue.Reclaim(ctx, p.options.ConsumerName, p.options.VisibilityTimeout, p.options.ReclaimBatchSize)
			if err != nil {
				if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
					return
				}
				log.Printf("task reclaim error: %v", err)
				continue
			}
			for _, delivery := range deliveries {
				select {
				case <-ctx.Done():
					return
				case out <- delivery:
				default:
					log.Printf("task reclaim buffer full task_id=%s message_id=%s", delivery.TaskID, delivery.MessageID)
				}
			}
		}
	}
}

func (p *TaskProcessor) handleDelivery(ctx context.Context, delivery queue.Delivery) {
	if strings.TrimSpace(delivery.TaskID) == "" {
		_ = p.taskQueue.Ack(ctx, delivery)
		return
	}

	if active, exists := p.activeDelivery(delivery.TaskID); exists {
		if active.messageID == delivery.MessageID {
			return
		}
		_ = p.taskQueue.Ack(ctx, delivery)
		return
	}

	if shouldDeadLetterPoisonDelivery(delivery, p.options.MaxAttempts) {
		p.finalizeFailed(delivery.TaskID, fmt.Errorf("task delivery exceeded max attempts"), int(delivery.DeliveryCount))
		_ = p.taskQueue.Ack(ctx, delivery)
		return
	}

	shouldAck := p.processWithRetry(ctx, delivery.TaskID, delivery.MessageID)
	if shouldAck {
		if err := p.taskQueue.Ack(ctx, delivery); err != nil {
			log.Printf("task ack error task_id=%s message_id=%s err=%v", delivery.TaskID, delivery.MessageID, err)
		}
	}
}

// processWithRetry 在未取消前提下执行有界重试。
func (p *TaskProcessor) processWithRetry(ctx context.Context, taskID string, messageIDs ...string) bool {
	messageID := ""
	if len(messageIDs) > 0 {
		messageID = messageIDs[0]
	}

	for attempt := 1; attempt <= p.options.MaxAttempts; attempt++ {
		if ctx.Err() != nil {
			return false
		}
		// 已取消任务直接走终结流程，不再继续执行。
		if p.isCanceled(taskID) {
			p.finalizeCanceled(taskID)
			return true
		}

		acquired, shouldAck := p.acquireExecutionLease(taskID)
		if !acquired {
			return shouldAck
		}

		if attempt > 1 {
			// 在事件流中写入重试标记，便于观测与排障。
			_, _ = p.taskStore.AppendEvent(taskID, domain.TaskEvent{
				Type:            "info",
				Message:         "retry_attempt",
				EmittedAtUnixMS: time.Now().UTC().UnixMilli(),
			})
		}

		err := p.processTask(ctx, taskID, messageID)
		if err == nil {
			_ = p.taskStore.ClearDeadLetter(taskID)
			return true
		}

		if ctx.Err() != nil && !p.isCanceled(taskID) {
			return false
		}

		// 取消属于终态，但不计入“失败重试”。
		if errors.Is(err, ErrTaskCanceled) {
			p.finalizeCanceled(taskID)
			return true
		}

		log.Printf("task processing failed task_id=%s attempt=%d err=%v", taskID, attempt, err)
		if !isRetryableProcessingError(err) {
			p.finalizeFailed(taskID, err, attempt)
			return true
		}

		if attempt < p.options.MaxAttempts {
			_, _ = p.taskStore.AppendEvent(taskID, domain.TaskEvent{
				Type:            "info",
				Message:         fmt.Sprintf("retrying after attempt %d/%d failed: %v", attempt, p.options.MaxAttempts, err),
				EmittedAtUnixMS: time.Now().UTC().UnixMilli(),
			})

			select {
			case <-ctx.Done():
				return false
			case <-time.After(p.options.RetryBackoff):
			}
			continue
		}

		// 最后一轮重试仍失败：标记失败并写入死信集合。
		p.finalizeFailed(taskID, err, attempt)
		return true
	}
	return true
}

// processTask 执行任务的一次尝试，并持久化收到的全部事件。
func (p *TaskProcessor) processTask(parentCtx context.Context, taskID string, messageIDs ...string) error {
	messageID := ""
	if len(messageIDs) > 0 {
		messageID = messageIDs[0]
	}

	task, ok := p.taskStore.Get(taskID)
	if !ok {
		// 任务不存在（被删除或未创建），无需处理。
		return nil
	}
	if task.Status == domain.TaskCanceled {
		return ErrTaskCanceled
	}

	// 每次尝试都绑定独立执行超时。
	execCtx, cancel := context.WithTimeout(parentCtx, p.options.ExecutionTimeout)
	p.registerActive(taskID, messageID, cancel)
	defer func() {
		p.unregisterActive(taskID)
		cancel()
	}()

	submissionTask := task
	agentEnabled := readMetadataBool(task.Metadata[metadataAgentEnabledKey], true)
	if !agentEnabled {
		if modelPrompt := strings.TrimSpace(task.Metadata[metadataModelPromptKey]); modelPrompt != "" {
			submissionTask.Prompt = modelPrompt
		}
	}

	stream, err := p.agent.SubmitTask(execCtx, submissionTask)
	if err != nil {
		if p.isCanceled(taskID) || isCanceledError(err) {
			return ErrTaskCanceled
		}
		return err
	}

	for {
		// 若 API 已将任务标记为 canceled，主动取消 gRPC 流上下文。
		if p.isCanceled(taskID) {
			cancel()
		}

		event, err := stream.Recv()
		if errors.Is(err, io.EOF) {
			if p.isCanceled(taskID) {
				return ErrTaskCanceled
			}
			// 防御性补偿：若 Runtime 未显式发送 completed 事件，则在 EOF 时补全 completed 状态。
			if currentTask, ok := p.taskStore.Get(taskID); ok && currentTask.Status == domain.TaskRunning {
				p.taskStore.UpdateStatus(taskID, domain.TaskCompleted, "")
			}
			return nil
		}
		if err != nil {
			if p.isCanceled(taskID) || isCanceledError(err) {
				return ErrTaskCanceled
			}
			return err
		}

		// 将 gRPC 枚举名转换为 HTTP/SSE 层使用的小写事件名。
		normalizedType := normalizeEventType(event.Type.String())
		info, diagnostics, hasInfo := agentinfo.FromProtoOrLegacy(event)
		for _, diagnostic := range diagnostics {
			log.Printf("agent info typed/legacy conflict task_id=%s %s", taskID, diagnostic)
		}
		taskEvent := domain.TaskEvent{
			Type:            normalizedType,
			Message:         event.Message,
			Token:           event.Token,
			TraceID:         event.TraceId,
			EmittedAtUnixMS: event.EmittedAtUnixMs,
		}
		if hasInfo {
			taskEvent.SchemaVersion = info.SchemaVersion
			taskEvent.EventName = info.EventName
			taskEvent.Payload = info.Payload
		}

		persistedEvent, persistErr := p.taskStore.AppendEvent(taskID, taskEvent)
		if persistErr != nil {
			if errors.Is(persistErr, store.ErrTaskNotFound) {
				// 会话删除可能与流式回写并发发生，任务已不存在时无需重试。
				return nil
			}
			return persistErr
		}

		// 某些事件类型会驱动任务状态迁移。
		if persistedEvent.Type == "info" {
			pauseMessage, metadataUpdates, shouldPause := parseApprovalRequiredEvent(persistedEvent)
			if shouldPause {
				if _, _, updateErr := p.taskStore.UpdateMetadata(taskID, metadataUpdates); updateErr != nil {
					return updateErr
				}

				if pauseMessage == "" {
					pauseMessage = "task paused: waiting for approval"
				}

				currentTask, exists := p.taskStore.Get(taskID)
				if !exists {
					return nil
				}
				if currentTask.Status != domain.TaskPaused {
					p.taskStore.UpdateStatus(taskID, domain.TaskPaused, pauseMessage)
					_, _ = p.taskStore.AppendEvent(taskID, domain.TaskEvent{
						Type:            "paused",
						Message:         pauseMessage,
						EmittedAtUnixMS: time.Now().UTC().UnixMilli(),
					})
				}
			}
		}

		switch persistedEvent.Type {
		case "completed":
			p.taskStore.UpdateStatus(taskID, domain.TaskCompleted, "")
			_, _, _ = p.taskStore.UpdateMetadata(taskID, map[string]string{
				metadataAgentResumeStepKey:   "",
				metadataAgentRequiredToolKey: "",
			})
		case "failed":
			p.taskStore.UpdateStatus(taskID, domain.TaskFailed, persistedEvent.Message)
		}
	}
}

// parseApprovalRequiredInfo 提取审批暂停信息并返回 metadata 更新补丁。
// 这里不解释或改写原始 info 事件，只把恢复所需的 tool call 字段旁路持久化，
// 让 API 审批接口后续能生成 approved_tool_call 并从暂停步骤继续执行。
func parseApprovalRequiredInfo(message string) (string, map[string]string, bool) {
	info, ok := agentinfo.FromLegacyMessage(message)
	if !ok {
		return "", nil, false
	}

	return parseApprovalRequiredAgentInfo(info)
}

func parseApprovalRequiredEvent(event domain.TaskEvent) (string, map[string]string, bool) {
	if event.EventName == "approval_required" && event.Payload != nil {
		return parseApprovalRequiredAgentInfo(agentinfo.AgentInfo{
			SchemaVersion: event.SchemaVersion,
			EventName:     event.EventName,
			Payload:       event.Payload,
		})
	}

	return parseApprovalRequiredInfo(event.Message)
}

func parseApprovalRequiredAgentInfo(info agentinfo.AgentInfo) (string, map[string]string, bool) {
	if strings.TrimSpace(info.EventName) != "approval_required" {
		return "", nil, false
	}

	tool := ""
	toolInput := ""
	riskLevel := ""
	approvalReason := ""
	stepIndex := 0
	if info.Payload != nil {
		tool = payloadString(info.Payload, "tool", "tool_name")
		toolInput = payloadString(info.Payload, "tool_input")
		riskLevel = payloadString(info.Payload, "risk_level")
		approvalReason = payloadString(info.Payload, "approval_reason", "reason")
		stepIndex = payloadInt(info.Payload, "resume_step_index")
	}

	updates := map[string]string{
		metadataApprovalGrantedKey: "false",
	}
	if stepIndex > 0 {
		updates[metadataAgentResumeStepKey] = strconv.Itoa(stepIndex)
	}
	if tool != "" {
		updates[metadataAgentRequiredToolKey] = tool
	}
	if toolInput != "" {
		updates[metadataAgentRequiredToolInputKey] = toolInput
	}
	if riskLevel != "" {
		updates[metadataAgentRequiredToolRiskKey] = riskLevel
	}
	if approvalReason != "" {
		updates[metadataAgentRequiredReasonKey] = approvalReason
	}

	pauseMessage := "task paused: waiting for approval"
	if tool != "" {
		pauseMessage = fmt.Sprintf("task paused: approval required for tool %s", tool)
	}

	return pauseMessage, updates, true
}

func payloadString(payload map[string]any, keys ...string) string {
	for _, key := range keys {
		raw, ok := payload[key]
		if !ok {
			continue
		}
		value := strings.TrimSpace(fmt.Sprint(raw))
		if value != "" {
			return value
		}
	}
	return ""
}

func payloadInt(payload map[string]any, keys ...string) int {
	for _, key := range keys {
		raw, ok := payload[key]
		if !ok {
			continue
		}
		switch value := raw.(type) {
		case int:
			return value
		case int32:
			return int(value)
		case int64:
			return int(value)
		case float64:
			return int(value)
		case string:
			parsed, err := strconv.Atoi(strings.TrimSpace(value))
			if err == nil {
				return parsed
			}
		}
	}
	return 0
}

// registerActive 记录当前活跃任务，供取消接口定位。
func (p *TaskProcessor) acquireExecutionLease(taskID string) (bool, bool) {
	leaseUntil := time.Now().UTC().Add(p.options.VisibilityTimeout)
	task, acquired, err := p.taskStore.AcquireExecutionLease(taskID, p.options.ConsumerName, leaseUntil)
	if err != nil {
		log.Printf("task lease acquire error task_id=%s consumer=%s err=%v", taskID, p.options.ConsumerName, err)
		return false, false
	}
	if acquired {
		return true, false
	}
	if task.ID == "" {
		return false, true
	}
	switch task.Status {
	case domain.TaskPaused, domain.TaskCompleted, domain.TaskFailed, domain.TaskCanceled:
		return false, true
	default:
		return false, true
	}
}

func (p *TaskProcessor) activeDelivery(taskID string) (activeExecution, bool) {
	p.activeMu.Lock()
	defer p.activeMu.Unlock()
	active, exists := p.active[taskID]
	return active, exists
}

func (p *TaskProcessor) registerActive(taskID string, messageID string, cancel context.CancelFunc) {
	p.activeMu.Lock()
	defer p.activeMu.Unlock()
	p.active[taskID] = activeExecution{cancel: cancel, messageID: messageID}
}

// unregisterActive 在尝试结束后移除活跃任务记录。
func (p *TaskProcessor) unregisterActive(taskID string) {
	p.activeMu.Lock()
	defer p.activeMu.Unlock()
	delete(p.active, taskID)
}

// isCanceled 基于持久化状态判断是否已取消，而非临时内存标记。
func (p *TaskProcessor) isCanceled(taskID string) bool {
	task, ok := p.taskStore.Get(taskID)
	if !ok {
		return false
	}
	return task.Status == domain.TaskCanceled
}

// finalizeCanceled 统一写入最终取消事件，并清理该任务的死信状态。
func (p *TaskProcessor) finalizeCanceled(taskID string) {
	cancelMessage := "canceled by user"
	if task, ok := p.taskStore.Get(taskID); ok {
		trimmed := strings.TrimSpace(task.Error)
		if trimmed != "" {
			cancelMessage = trimmed
		}
	}

	p.taskStore.UpdateStatus(taskID, domain.TaskCanceled, cancelMessage)
	_, _ = p.taskStore.AppendEvent(taskID, domain.TaskEvent{
		Type:            "canceled",
		Message:         "task canceled",
		EmittedAtUnixMS: time.Now().UTC().UnixMilli(),
	})
	_ = p.taskStore.ClearDeadLetter(taskID)
}

// finalizeFailed 统一写入失败状态、失败事件和死信记录。
func (p *TaskProcessor) finalizeFailed(taskID string, err error, attempts int) {
	errMessage := "task processing failed"
	if err != nil {
		errMessage = err.Error()
	}

	p.taskStore.UpdateStatus(taskID, domain.TaskFailed, errMessage)
	_ = p.taskStore.MarkDeadLetter(taskID, errMessage, attempts)
	_, _ = p.taskStore.AppendEvent(taskID, domain.TaskEvent{
		Type:            "failed",
		Message:         errMessage,
		EmittedAtUnixMS: time.Now().UTC().UnixMilli(),
	})
	_, _ = p.taskStore.AppendEvent(taskID, domain.TaskEvent{
		Type:            "dead_lettered",
		Message:         "task moved to dead letter queue",
		EmittedAtUnixMS: time.Now().UTC().UnixMilli(),
	})
}

// isRetryableProcessingError 判断某次处理错误是否值得继续重试。
func isRetryableProcessingError(err error) bool {
	if err == nil {
		return false
	}

	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return false
	}

	grpcStatus, ok := status.FromError(err)
	if !ok {
		return true
	}

	switch grpcStatus.Code() {
	case codes.DeadlineExceeded,
		codes.InvalidArgument,
		codes.PermissionDenied,
		codes.Unauthenticated,
		codes.Unimplemented,
		codes.FailedPrecondition:
		return false
	default:
		return true
	}
}

// isCanceledError 把传输层取消信号映射为领域层“已取消”。
func isCanceledError(err error) bool {
	if err == nil {
		return false
	}

	if errors.Is(err, context.Canceled) {
		return true
	}

	grpcStatus, ok := status.FromError(err)
	if !ok {
		return false
	}

	return grpcStatus.Code() == codes.Canceled
}

// normalizeEventType 将 protobuf 枚举名转换为 API 对外事件标签。
func normalizeEventType(raw string) string {
	value := strings.TrimPrefix(raw, "AGENT_EVENT_TYPE_")
	if value == "" {
		return "unspecified"
	}
	return strings.ToLower(value)
}

func readMetadataBool(value string, defaultValue bool) bool {
	normalized := strings.ToLower(strings.TrimSpace(value))
	switch normalized {
	case "1", "true", "yes", "on", "y":
		return true
	case "0", "false", "no", "off", "n":
		return false
	default:
		return defaultValue
	}
}

func shouldDeadLetterPoisonDelivery(delivery queue.Delivery, maxAttempts int) bool {
	if maxAttempts <= 0 || delivery.DeliveryCount <= 0 {
		return false
	}
	return delivery.DeliveryCount > int64(maxAttempts)
}
