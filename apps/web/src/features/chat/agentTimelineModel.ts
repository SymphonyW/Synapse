import type { AgentTimelineItem, Language, StreamEvent, Task } from '../../shared/types/domain'
import { truncatePreview } from '../../shared/utils/format'
import {
  collectSourceLinks,
  isRecord,
  parseAgentInfoEnvelope,
  readRecordNumber,
  readRecordString,
  readStringArray,
} from '../../shared/utils/records'

type Translate = (zh: string, en: string) => string

function agentTimelineLabel(agentEvent: string, tr: Translate): string {
  switch (agentEvent) {
    case 'plan':
      return tr('计划步骤', 'Plan Steps')
    case 'memory_recall':
      return tr('记忆命中', 'Memory Hits')
    case 'tool_selected':
      return tr('选择工具', 'Tool Selected')
    case 'tool_started':
      return tr('开始工具', 'Tool Started')
    case 'tool_finished':
      return tr('工具完成', 'Tool Finished')
    case 'tool_failed':
      return tr('工具失败', 'Tool Failed')
    case 'tool_skipped':
      return tr('跳过工具', 'Tool Skipped')
    case 'approval_required':
      return tr('需要审批', 'Approval Required')
    case 'observe':
      return tr('观察结果', 'Observation')
    case 'replan':
      return tr('重新规划', 'Replan')
    default:
      return `agent.${agentEvent}`
  }
}

export function summarizeTaskError(errorText: string | undefined, tr: Translate): string {
  const normalized = (errorText ?? '').trim()
  if (normalized === '') {
    return tr('任务执行失败。', 'Task failed during execution.')
  }

  const lowered = normalized.toLowerCase()
  if (
    lowered.includes('resource_exhausted') ||
    lowered.includes('quota') ||
    lowered.includes('rate limit') ||
    lowered.includes('429')
  ) {
    return tr(
      '模型服务触发配额或限流，请稍后重试。',
      'Model provider quota or rate limit reached. Please retry later.',
    )
  }

  if (
    lowered.includes('invalid api key') ||
    lowered.includes('unauthorized') ||
    lowered.includes('permission denied')
  ) {
    return tr(
      '模型服务鉴权失败，请检查 API Key 与服务配置。',
      'Model provider authentication failed. Please verify API key and provider config.',
    )
  }

  return normalized.length > 260 ? `${normalized.slice(0, 260)}...` : normalized
}

export function assistantTextForTask(
  task: Task,
  responseByTaskID: Record<string, string>,
  tr: Translate,
): string {
  const cached = responseByTaskID[task.id]
  if (cached && cached.trim() !== '') {
    return cached
  }

  switch (task.status) {
    case 'queued':
      return tr('正在排队处理中...', 'Queued and waiting for execution...')
    case 'running':
      return tr('正在生成回复...', 'Generating response...')
    case 'completed':
      return tr(
        '该任务已完成，但本地尚未缓存回复内容。',
        'Task completed but response text is not cached yet.',
      )
    case 'paused':
      return task.error || tr('任务已暂停，等待审批恢复。', 'Task is paused and waiting for approval.')
    case 'failed':
      return summarizeTaskError(task.error, tr)
    case 'canceled':
      return task.error || tr('任务已取消。', 'Task was canceled.')
    default:
      return tr('等待回复中。', 'Waiting for response.')
  }
}

export function buildAgentTimelineItems(
  task: Task,
  taskEvents: StreamEvent[],
  finalAnswer: string,
  language: Language,
  tr: Translate,
): AgentTimelineItem[] {
  const items: AgentTimelineItem[] = []

  taskEvents.forEach((event, index) => {
    const envelope = parseAgentInfoEnvelope(event.message)
    if (!envelope?.agent_event) {
      return
    }

    const payload = envelope.payload
    const baseID = `${event.event_id ?? index}-${envelope.agent_event}`
    const eventTime = event.emitted_at_unix_ms

    if (envelope.agent_event === 'plan') {
      const steps = readStringArray(payload?.steps)
      const stepCount = readRecordNumber(payload, 'step_count') ?? steps.length
      items.push({
        id: baseID,
        kind: 'plan',
        title: agentTimelineLabel(envelope.agent_event, tr),
        detail:
          stepCount > 0
            ? language === 'zh'
              ? `${stepCount} 个步骤`
              : `${stepCount} steps`
            : tr('等待规划结果', 'Waiting for plan output'),
        time: eventTime,
        status: 'neutral',
        meta: [],
        links: [],
        bullets: steps.slice(0, 6),
      })
      return
    }

    if (envelope.agent_event === 'memory_recall') {
      const hits = Array.isArray(payload?.hits) ? payload.hits.filter(isRecord) : []
      const hitCount = readRecordNumber(payload, 'hit_count') ?? hits.length
      items.push({
        id: baseID,
        kind: 'memory',
        title: agentTimelineLabel(envelope.agent_event, tr),
        detail:
          hitCount > 0
            ? language === 'zh'
              ? `召回 ${hitCount} 条长期记忆`
              : `${hitCount} long-term memories recalled`
            : tr('没有命中长期记忆', 'No long-term memory hit'),
        time: eventTime,
        status: hitCount > 0 ? 'ok' : 'neutral',
        meta: [],
        links: [],
        bullets: hits.slice(0, 4).map((hit) => {
          const summary = readRecordString(hit, 'summary')
          const preview = readRecordString(hit, 'content_preview')
          const score = readRecordNumber(hit, 'score')
          const content = summary || preview || tr('未命名记忆', 'Untitled memory')
          return typeof score === 'number' ? `${content} · ${score}` : content
        }),
      })
      return
    }

    if (envelope.agent_event === 'approval_required') {
      const toolName = readRecordString(payload, 'tool_name') || readRecordString(payload, 'tool')
      const toolInput = readRecordString(payload, 'tool_input')
      const riskLevel = readRecordString(payload, 'risk_level')
      const approvalReason = readRecordString(payload, 'approval_reason') || readRecordString(payload, 'reason')
      const approvedToolCall = isRecord(payload?.approved_tool_call)
        ? payload.approved_tool_call
        : undefined
      const resumeStep =
        readRecordNumber(payload, 'resume_step_index') ??
        readRecordNumber(approvedToolCall, 'resume_step_index')
      items.push({
        id: baseID,
        kind: 'approval',
        title: agentTimelineLabel(envelope.agent_event, tr),
        detail:
          toolInput ||
          approvalReason ||
          tr('等待人工确认后恢复执行', 'Waiting for human approval to resume'),
        time: eventTime,
        status: 'warning',
        meta: [
          toolName && `${tr('工具', 'Tool')}: ${toolName}`,
          riskLevel && `${tr('风险', 'Risk')}: ${riskLevel}`,
          typeof resumeStep === 'number' && `${tr('恢复步骤', 'Resume Step')}: ${resumeStep}`,
        ].filter((item): item is string => Boolean(item)),
        links: [],
        bullets: approvalReason ? [approvalReason] : [],
      })
      return
    }

    if (envelope.agent_event.startsWith('tool_')) {
      const toolName = readRecordString(payload, 'tool')
      const toolInput = readRecordString(payload, 'tool_input')
      const output = readRecordString(payload, 'output_preview') || readRecordString(payload, 'output')
      const reason = readRecordString(payload, 'reason')
      const riskLevel = readRecordString(payload, 'risk_level')
      const duration = readRecordNumber(payload, 'duration_ms')
      const links = collectSourceLinks(payload)
      const status =
        envelope.agent_event === 'tool_finished'
          ? 'ok'
          : envelope.agent_event === 'tool_failed'
            ? 'error'
            : envelope.agent_event === 'tool_skipped'
              ? 'warning'
              : 'neutral'

      items.push({
        id: baseID,
        kind: links.length > 0 ? 'source' : 'tool',
        title: toolName
          ? `${agentTimelineLabel(envelope.agent_event, tr)} · ${toolName}`
          : agentTimelineLabel(envelope.agent_event, tr),
        detail: output || toolInput || reason || envelope.display_message || '',
        time: eventTime,
        status,
        meta: [
          riskLevel && `${tr('风险', 'Risk')}: ${riskLevel}`,
          typeof duration === 'number' && `${duration}ms`,
        ].filter((item): item is string => Boolean(item)),
        links,
        bullets: reason && reason !== output ? [reason] : [],
      })
    }
  })

  const cachedFinalAnswer = finalAnswer.trim()
  if (task.status === 'completed' && cachedFinalAnswer !== '') {
    items.push({
      id: `${task.id}-final-answer`,
      kind: 'final',
      title: tr('最终回答', 'Final Answer'),
      detail: truncatePreview(cachedFinalAnswer, 260),
      time: new Date(task.updated_at).getTime(),
      status: 'ok',
      meta: [`${cachedFinalAnswer.length} chars`],
      links: [],
      bullets: [],
    })
  }

  return items
}
