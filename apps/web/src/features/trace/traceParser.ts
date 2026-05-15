import type {
  AgentInfoEnvelope,
  ParsedTrace,
  TraceApproval,
  TraceDiagnosis,
  TraceEvaluation,
  TraceExportSummary,
  TraceMemoryHit,
  TraceMemoryRecall,
  TraceMemoryWrite,
  TraceRawEvent,
  TraceReplan,
  TraceStage,
  TraceStep,
  TraceSynthesisMode,
  TraceTaskContext,
  TraceToolCall,
  TraceToolStatus,
} from './traceTypes'

const TERMINAL_TOOL_STATUSES = new Set<TraceToolStatus>([
  'finished',
  'failed',
  'skipped',
  'approval_required',
])

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

const readString = (record: Record<string, unknown> | undefined, key: string): string | undefined =>
  typeof record?.[key] === 'string' ? (record[key] as string) : undefined

const readNumber = (record: Record<string, unknown> | undefined, key: string): number | undefined =>
  typeof record?.[key] === 'number' ? (record[key] as number) : undefined

const readBoolean = (record: Record<string, unknown> | undefined, key: string): boolean | undefined =>
  typeof record?.[key] === 'boolean' ? (record[key] as boolean) : undefined

const readStringArray = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []

const eventID = (event: TraceRawEvent, fallback: number): string =>
  String(event.event_id ?? `idx-${fallback}`)

const makeNodeID = (event: TraceRawEvent, index: number, suffix: string): string =>
  `${eventID(event, index)}-${suffix}`

export function parseAgentInfoEnvelope(message?: string): AgentInfoEnvelope | null {
  if (!message?.trim()) {
    return null
  }

  try {
    const parsed = JSON.parse(message) as unknown
    if (!isRecord(parsed) || typeof parsed.agent_event !== 'string') {
      return null
    }

    return {
      schema: readString(parsed, 'schema'),
      agent_event: readString(parsed, 'agent_event'),
      display_message: readString(parsed, 'display_message'),
      payload: isRecord(parsed.payload) ? parsed.payload : undefined,
    }
  } catch {
    return null
  }
}

function getOrCreateStep(steps: Map<number, TraceStep>, index: number, objective?: string): TraceStep {
  const current = steps.get(index)
  if (current) {
    if (!current.objective && objective) {
      current.objective = objective
    }
    return current
  }

  const step: TraceStep = {
    index,
    objective,
    toolCalls: [],
    observations: [],
    reflections: [],
    replans: [],
    approvals: [],
  }
  steps.set(index, step)
  return step
}

function findToolCall(step: TraceStep, toolName: string): TraceToolCall | undefined {
  for (let index = step.toolCalls.length - 1; index >= 0; index -= 1) {
    const call = step.toolCalls[index]
    if (call.toolName !== toolName) {
      continue
    }

    if (!TERMINAL_TOOL_STATUSES.has(call.status) || call.status === 'approval_required') {
      return call
    }
  }

  return undefined
}

function createToolCall(
  event: TraceRawEvent,
  index: number,
  stepIndex: number,
  payload: Record<string, unknown> | undefined,
  status: TraceToolStatus,
): TraceToolCall {
  return {
    id: makeNodeID(event, index, `tool-${readString(payload, 'tool') ?? 'unknown'}`),
    stepIndex,
    objective: readString(payload, 'objective'),
    toolName: readString(payload, 'tool') ?? readString(payload, 'tool_name') ?? 'unknown',
    riskLevel: readString(payload, 'risk_level'),
    inputPreview: readString(payload, 'tool_input'),
    status,
    durationMs: readNumber(payload, 'duration_ms'),
    ok: readBoolean(payload, 'ok'),
    failureReason:
      readString(payload, 'reason') ??
      (isRecord(payload?.error) ? readString(payload.error, 'message') : undefined),
    outputPreview: readString(payload, 'output_preview') ?? readString(payload, 'output'),
    requiresApproval: readBoolean(payload, 'requires_approval'),
  }
}

function upsertToolCall(
  step: TraceStep,
  event: TraceRawEvent,
  index: number,
  payload: Record<string, unknown> | undefined,
  status: TraceToolStatus,
): TraceToolCall {
  const toolName = readString(payload, 'tool') ?? readString(payload, 'tool_name') ?? 'unknown'
  const existing = findToolCall(step, toolName)
  const call = existing ?? createToolCall(event, index, step.index, payload, status)

  if (!existing) {
    step.toolCalls.push(call)
  }

  call.objective = call.objective ?? readString(payload, 'objective')
  call.riskLevel = readString(payload, 'risk_level') ?? call.riskLevel
  call.inputPreview = readString(payload, 'tool_input') ?? call.inputPreview
  call.durationMs = readNumber(payload, 'duration_ms') ?? call.durationMs
  call.ok = readBoolean(payload, 'ok') ?? call.ok
  call.failureReason =
    readString(payload, 'reason') ??
    (isRecord(payload?.error) ? readString(payload.error, 'message') : undefined) ??
    call.failureReason
  call.outputPreview = readString(payload, 'output_preview') ?? readString(payload, 'output') ?? call.outputPreview
  call.requiresApproval = readBoolean(payload, 'requires_approval') ?? call.requiresApproval

  if (status === 'selected') {
    call.selectedAt = event.emitted_at_unix_ms
  }
  if (status === 'running') {
    call.startedAt = event.emitted_at_unix_ms
  }
  if (status === 'finished' || status === 'failed' || status === 'skipped') {
    call.finishedAt = event.emitted_at_unix_ms
  }
  call.status = status

  return call
}

function memoryHitFromRecord(hit: Record<string, unknown>): TraceMemoryHit {
  return {
    memoryId: readString(hit, 'memory_id'),
    summary: readString(hit, 'summary'),
    contentPreview: readString(hit, 'content_preview'),
    sourceTaskId: readString(hit, 'source_task_id'),
    importance: readNumber(hit, 'importance'),
    createdAt: readNumber(hit, 'created_at'),
    score: readNumber(hit, 'score'),
    matchedTerms: readStringArray(hit.matched_terms),
  }
}

function buildStages(trace: Omit<ParsedTrace, 'stages' | 'diagnosis'>): TraceStage[] {
  const stages: TraceStage[] = []

  if (trace.perceive) {
    stages.push({
      id: 'perceive',
      kind: 'perceive',
      title: '感知',
      subtitle: trace.perceive.shortContextCount
        ? `上下文 ${trace.perceive.shortContextCount}`
        : undefined,
      status: 'neutral',
      time: trace.perceive.time,
    })
  }

  if (trace.memoryRecall) {
    stages.push({
      id: 'memory-recall',
      kind: 'memory_recall',
      title: '记忆召回',
      subtitle: `${trace.memoryRecall.hitCount} 命中`,
      status: trace.memoryRecall.hitCount > 0 ? 'ok' : 'neutral',
      time: trace.memoryRecall.time,
    })
  }

  if (trace.plan) {
    stages.push({
      id: 'plan',
      kind: 'plan',
      title: '规划',
      subtitle: `${trace.plan.stepCount} 步`,
      status: 'neutral',
      time: trace.plan.time,
    })
  }

  trace.steps.forEach((step) => {
    const hasFailure = step.toolCalls.some((call) => call.status === 'failed')
    const hasApproval = step.approvals.length > 0
    const hasSuccess = step.toolCalls.some((call) => call.status === 'finished')
    stages.push({
      id: `step-${step.index}`,
      kind: 'step',
      title: `Step ${step.index}`,
      subtitle: step.objective,
      status: hasFailure ? 'error' : hasApproval ? 'warning' : hasSuccess ? 'ok' : 'neutral',
      time: step.actedAt,
      stepIndex: step.index,
    })
  })

  const latestSynthesisMode = trace.synthesisModes.at(-1)
  if (latestSynthesisMode) {
    stages.push({
      id: 'synthesis-mode',
      kind: 'synthesis_mode',
      title: '综合输出',
      subtitle: latestSynthesisMode.mode,
      status: 'neutral',
      time: latestSynthesisMode.time,
    })
  }

  if (trace.memoryWrites.length > 0) {
    stages.push({
      id: 'memory-write',
      kind: 'memory_write',
      title: '记忆写入',
      subtitle: `${trace.memoryWrites.length} 次`,
      status: 'ok',
      time: trace.memoryWrites.at(-1)?.time,
    })
  }

  if (trace.evaluation) {
    stages.push({
      id: 'evaluate',
      kind: 'evaluate',
      title: '评估',
      subtitle:
        typeof trace.evaluation.estimatedSuccess === 'number'
          ? `成功率 ${Math.round(trace.evaluation.estimatedSuccess * 100)}%`
          : undefined,
      status:
        typeof trace.evaluation.estimatedSuccess === 'number' &&
        trace.evaluation.estimatedSuccess >= 0.8
          ? 'ok'
          : 'neutral',
      time: trace.evaluation.time,
    })
  }

  return stages
}

function buildDiagnosis(trace: Omit<ParsedTrace, 'stages' | 'diagnosis'>, events: TraceRawEvent[]): TraceDiagnosis {
  const toolCalls = trace.steps.flatMap((step) =>
    step.toolCalls.filter((call) => call.toolName !== 'none' && call.status !== 'selected'),
  )
  const lastFailedTool = [...toolCalls].reverse().find((call) => call.status === 'failed')
  const failedEvent = [...events].reverse().find((event) => event.type === 'failed')

  return {
    toolCallCount: toolCalls.length,
    successfulToolCount: toolCalls.filter((call) => call.status === 'finished').length,
    failedToolCount: toolCalls.filter((call) => call.status === 'failed').length,
    hasApprovalPause: trace.approvals.length > 0,
    hasReplan: trace.replans.length > 0,
    blockedActions: trace.evaluation?.blockedActions,
    lastFailureReason:
      lastFailedTool?.failureReason ||
      failedEvent?.message ||
      failedEvent?.status ||
      trace.task.error,
  }
}

export function parseTrace(events: TraceRawEvent[], task: TraceTaskContext): ParsedTrace {
  const steps = new Map<number, TraceStep>()
  const approvals: TraceApproval[] = []
  const replans: TraceReplan[] = []
  const synthesisModes: TraceSynthesisMode[] = []
  const memoryWrites: TraceMemoryWrite[] = []
  const parseErrors: string[] = []
  let perceive: ParsedTrace['perceive']
  let memoryRecall: TraceMemoryRecall | undefined
  let plan: ParsedTrace['plan']
  let evaluation: TraceEvaluation | undefined

  events.forEach((event, index) => {
    if (event.type !== 'info') {
      return
    }

    const envelope = parseAgentInfoEnvelope(event.message)
    if (!envelope?.agent_event) {
      if (event.message?.trim()) {
        parseErrors.push(`event ${eventID(event, index)}: invalid agent info envelope`)
      }
      return
    }

    const payload = envelope.payload
    const stepIndex = readNumber(payload, 'step_index')
    const stepObjective = readString(payload, 'objective')

    switch (envelope.agent_event) {
      case 'perceive':
        perceive = {
          eventId: event.event_id,
          time: event.emitted_at_unix_ms,
          taskId: readString(payload, 'task_id'),
          shortContextCount: readNumber(payload, 'short_context_count'),
          recalledMemoryCount: readNumber(payload, 'recalled_memory_count'),
        }
        break
      case 'memory_recall': {
        const hits = Array.isArray(payload?.hits) ? payload.hits.filter(isRecord) : []
        memoryRecall = {
          eventId: event.event_id,
          time: event.emitted_at_unix_ms,
          query: readString(payload, 'query'),
          hitCount: readNumber(payload, 'hit_count') ?? hits.length,
          hits: hits.map(memoryHitFromRecord),
        }
        break
      }
      case 'plan':
        plan = {
          eventId: event.event_id,
          time: event.emitted_at_unix_ms,
          stepCount: readNumber(payload, 'step_count') ?? readStringArray(payload?.steps).length,
          steps: readStringArray(payload?.steps),
        }
        break
      case 'act':
        if (typeof stepIndex === 'number') {
          const step = getOrCreateStep(steps, stepIndex, stepObjective)
          step.actedAt = event.emitted_at_unix_ms
        }
        break
      case 'tool_selected':
      case 'tool_started':
      case 'tool_finished':
      case 'tool_failed':
      case 'tool_skipped':
        if (typeof stepIndex === 'number') {
          const step = getOrCreateStep(steps, stepIndex, stepObjective)
          const statusByEvent: Record<string, TraceToolStatus> = {
            tool_selected: 'selected',
            tool_started: 'running',
            tool_finished: 'finished',
            tool_failed: 'failed',
            tool_skipped: 'skipped',
          }
          upsertToolCall(step, event, index, payload, statusByEvent[envelope.agent_event])
        }
        break
      case 'approval_required': {
        const approval: TraceApproval = {
          id: makeNodeID(event, index, 'approval'),
          stepIndex,
          toolName: readString(payload, 'tool_name') ?? readString(payload, 'tool'),
          toolInput: readString(payload, 'tool_input'),
          riskLevel: readString(payload, 'risk_level'),
          reason: readString(payload, 'approval_reason') ?? readString(payload, 'reason'),
          resumeStepIndex: readNumber(payload, 'resume_step_index'),
          time: event.emitted_at_unix_ms,
        }
        approvals.push(approval)
        if (typeof stepIndex === 'number') {
          const step = getOrCreateStep(steps, stepIndex, stepObjective)
          step.approvals.push(approval)
          upsertToolCall(step, event, index, payload, 'approval_required')
        }
        break
      }
      case 'observe':
        if (typeof stepIndex === 'number') {
          const step = getOrCreateStep(steps, stepIndex, stepObjective)
          step.observations.push({
            id: makeNodeID(event, index, 'observe'),
            toolName: readString(payload, 'tool'),
            status: readString(payload, 'status'),
            observation: readString(payload, 'observation'),
            reason: readString(payload, 'reason'),
            replanned: readBoolean(payload, 'replanned'),
            time: event.emitted_at_unix_ms,
          })
        }
        break
      case 'reflect':
        if (typeof stepIndex === 'number') {
          const step = getOrCreateStep(steps, stepIndex, stepObjective)
          step.reflections.push({
            id: makeNodeID(event, index, 'reflect'),
            reflection: readString(payload, 'reflection'),
            replanned: readBoolean(payload, 'replanned'),
            time: event.emitted_at_unix_ms,
          })
        }
        break
      case 'replan': {
        const replan: TraceReplan = {
          id: makeNodeID(event, index, 'replan'),
          stepIndex,
          reason: readString(payload, 'reason'),
          fromTool: readString(payload, 'from_tool'),
          toTool: readString(payload, 'to_tool'),
          toToolInput: readString(payload, 'to_tool_input'),
          time: event.emitted_at_unix_ms,
        }
        replans.push(replan)
        if (typeof stepIndex === 'number') {
          getOrCreateStep(steps, stepIndex, stepObjective).replans.push(replan)
        }
        break
      }
      case 'synthesis_mode':
        synthesisModes.push({
          id: makeNodeID(event, index, 'synthesis'),
          mode: readString(payload, 'mode'),
          time: event.emitted_at_unix_ms,
        })
        break
      case 'memory_write':
        memoryWrites.push({
          id: makeNodeID(event, index, 'memory-write'),
          memoryId: readString(payload, 'memory_id'),
          summary: readString(payload, 'summary'),
          contentPreview: readString(payload, 'content_preview'),
          sourceTaskId: readString(payload, 'source_task_id'),
          importance: readNumber(payload, 'importance'),
          createdAt: readNumber(payload, 'created_at'),
          time: event.emitted_at_unix_ms,
        })
        break
      case 'evaluate':
        evaluation = {
          id: makeNodeID(event, index, 'evaluate'),
          estimatedSuccess: readNumber(payload, 'estimated_success'),
          objectiveCompletion: readNumber(payload, 'objective_completion'),
          toolSuccessRate: readNumber(payload, 'tool_success_rate'),
          blockedActions: readNumber(payload, 'blocked_actions'),
          durationMs: readNumber(payload, 'duration_ms'),
          time: event.emitted_at_unix_ms,
        }
        break
      default:
        break
    }
  })

  if (plan) {
    plan.steps.forEach((objective, index) => {
      getOrCreateStep(steps, index + 1, objective)
    })
  }

  const orderedSteps = [...steps.values()].sort((left, right) => left.index - right.index)
  const baseTrace = {
    task,
    perceive,
    memoryRecall,
    plan,
    steps: orderedSteps,
    approvals,
    replans,
    synthesisModes,
    memoryWrites,
    evaluation,
    parseErrors,
  }

  return {
    ...baseTrace,
    stages: buildStages(baseTrace),
    diagnosis: buildDiagnosis(baseTrace, events),
  }
}

export function createTraceExportSummary(trace: ParsedTrace): TraceExportSummary {
  return {
    stageCount: trace.stages.length,
    stepCount: trace.steps.length,
    memoryHitCount: trace.memoryRecall?.hitCount ?? 0,
    toolCallCount: trace.diagnosis.toolCallCount,
    successfulToolCount: trace.diagnosis.successfulToolCount,
    failedToolCount: trace.diagnosis.failedToolCount,
    approvalCount: trace.approvals.length,
    replanCount: trace.replans.length,
    synthesisMode: trace.synthesisModes.at(-1)?.mode,
    memoryWriteCount: trace.memoryWrites.length,
    evaluation: trace.evaluation
      ? {
          estimatedSuccess: trace.evaluation.estimatedSuccess,
          objectiveCompletion: trace.evaluation.objectiveCompletion,
          toolSuccessRate: trace.evaluation.toolSuccessRate,
          blockedActions: trace.evaluation.blockedActions,
          durationMs: trace.evaluation.durationMs,
        }
      : undefined,
  }
}
