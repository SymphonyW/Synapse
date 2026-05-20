import type { ApprovedToolCallPayload, Task } from '../../shared/types/domain'

type Translate = (zh: string, en: string) => string

export type ApproveTaskPayload = {
  requested_by: string
  reason: string
  approved_tools?: string[]
  approved_tool_call?: ApprovedToolCallPayload
}

export type ApprovalRequestSummary = {
  toolName: string
  toolInput: string
  riskLevel: string
  reason: string
  resumeStepIndex: number
  statusLabel: string
}

type BuildApprovalPayloadOptions = {
  fallbackApprovedTools?: string[]
  reason?: string
}

function metadataValue(task: Task, key: string): string {
  return (task.metadata?.[key] ?? '').trim()
}

function parseResumeStepIndex(rawValue: string): number {
  const parsed = Number.parseInt(rawValue, 10)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 0
}

function uniqueNormalizedTools(tools: string[]): string[] {
  const seen = new Set<string>()
  const normalized: string[] = []

  tools.forEach((tool) => {
    const value = tool.trim().toLowerCase()
    if (value === '' || seen.has(value)) {
      return
    }
    seen.add(value)
    normalized.push(value)
  })

  return normalized
}

export function defaultApprovalReason(tr: Translate): string {
  return tr('用户审批通过并恢复任务', 'Task approved and resumed by user')
}

export function isTaskApprovalRequired(task: Task): boolean {
  return task.status === 'paused' && metadataValue(task, 'agent_required_tool') !== ''
}

export function buildApprovedToolCallFromTask(
  task: Task,
  tr: Translate,
  fallbackReason = defaultApprovalReason(tr),
): ApprovedToolCallPayload | null {
  const toolName = metadataValue(task, 'agent_required_tool')
  const toolInput = metadataValue(task, 'agent_required_tool_input')
  if (toolName === '' || toolInput === '') {
    return null
  }

  return {
    tool_name: toolName,
    tool_input: toolInput,
    risk_level: metadataValue(task, 'agent_required_tool_risk_level'),
    reason: metadataValue(task, 'agent_required_reason') || fallbackReason,
    resume_step_index: parseResumeStepIndex(metadataValue(task, 'agent_resume_step_index')),
  }
}

export function buildApprovalPayloadFromTask(
  task: Task,
  requestedBy: string,
  tr: Translate,
  options: BuildApprovalPayloadOptions = {},
): ApproveTaskPayload {
  const reason = options.reason ?? defaultApprovalReason(tr)
  const approvedToolCall = buildApprovedToolCallFromTask(task, tr, reason)
  if (approvedToolCall) {
    return {
      requested_by: requestedBy,
      reason,
      approved_tool_call: approvedToolCall,
    }
  }

  const requiredTool = metadataValue(task, 'agent_required_tool')
  const approvedTools = uniqueNormalizedTools([
    requiredTool,
    ...(options.fallbackApprovedTools ?? []),
  ])

  return {
    requested_by: requestedBy,
    reason,
    ...(approvedTools.length > 0 ? { approved_tools: approvedTools } : {}),
  }
}

export function getApprovalRequestSummary(task: Task, tr: Translate): ApprovalRequestSummary {
  return {
    toolName: metadataValue(task, 'agent_required_tool') || tr('未知工具', 'Unknown tool'),
    toolInput: metadataValue(task, 'agent_required_tool_input'),
    riskLevel: metadataValue(task, 'agent_required_tool_risk_level') || tr('未知', 'unknown'),
    reason:
      metadataValue(task, 'agent_required_reason') ||
      task.error?.trim() ||
      tr('高风险工具调用需要用户审批。', 'High-risk tool call requires user approval.'),
    resumeStepIndex: parseResumeStepIndex(metadataValue(task, 'agent_resume_step_index')),
    statusLabel: tr('等待用户审批', 'Waiting for user approval'),
  }
}
