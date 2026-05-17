import type {
  ParsedTrace,
  TraceEvaluation,
  TraceRawEvent,
  TraceToolStatus,
} from './traceTypes'

type ComparableMetric = {
  base: number
  replay: number
  delta: number
  changed: boolean
}

type ComparableFlag = {
  base: boolean
  replay: boolean
  changed: boolean
}

type ComparableValue = {
  base: string
  replay: string
  changed: boolean
}

export type ToolSequenceRow = {
  kind: 'same' | 'changed' | 'added' | 'removed'
  base?: ReplayToolSnapshot
  replay?: ReplayToolSnapshot
}

export type ReplayToolSnapshot = {
  toolName: string
  status: TraceToolStatus
  stepIndex: number
}

export type ReplayTextDiffRow = {
  kind: 'same' | 'added' | 'removed'
  text: string
}

export type ReplayDiff = {
  summary: {
    status: ComparableValue
    durationMs: ComparableMetric
    planStepCount: ComparableMetric
    toolCallCount: ComparableMetric
    successfulToolCount: ComparableMetric
    failedToolCount: ComparableMetric
    approvalRequired: ComparableFlag
    replan: ComparableFlag
    memoryRecallHits: ComparableMetric
    finalAnswerLength: ComparableMetric
  }
  evaluateMetrics: {
    estimatedSuccess: ComparableMetric
    objectiveCompletion: ComparableMetric
    toolSuccessRate: ComparableMetric
    blockedActions: ComparableMetric
    durationMs: ComparableMetric
  }
  toolSequence: {
    rows: ToolSequenceRow[]
  }
  finalAnswerDiff: ReplayTextDiffRow[]
  finalAnswers: {
    base: string
    replay: string
  }
  traceCompleteness: {
    base: TraceCompleteness
    replay: TraceCompleteness
  }
}

type TraceCompleteness = {
  hasPlan: boolean
  hasMemoryRecall: boolean
  hasEvaluation: boolean
  hasTokenOutput: boolean
}

type BuildReplayDiffInput = {
  baseTrace: ParsedTrace
  replayTrace: ParsedTrace
  baseEvents: TraceRawEvent[]
  replayEvents: TraceRawEvent[]
}

const terminalStatuses = new Set<TraceToolStatus>([
  'finished',
  'failed',
  'skipped',
  'approval_required',
])

export function buildReplayDiff({
  baseTrace,
  replayTrace,
  baseEvents,
  replayEvents,
}: BuildReplayDiffInput): ReplayDiff {
  const baseTools = flattenToolSequence(baseTrace)
  const replayTools = flattenToolSequence(replayTrace)
  const baseAnswer = collectFinalAnswer(baseEvents)
  const replayAnswer = collectFinalAnswer(replayEvents)

  return {
    summary: {
      status: compareValue(String(baseTrace.task.status ?? ''), String(replayTrace.task.status ?? '')),
      durationMs: compareMetric(resolveDurationMs(baseTrace, baseEvents), resolveDurationMs(replayTrace, replayEvents)),
      planStepCount: compareMetric(resolvePlanStepCount(baseTrace), resolvePlanStepCount(replayTrace)),
      toolCallCount: compareMetric(baseTrace.diagnosis.toolCallCount, replayTrace.diagnosis.toolCallCount),
      successfulToolCount: compareMetric(
        baseTrace.diagnosis.successfulToolCount,
        replayTrace.diagnosis.successfulToolCount,
      ),
      failedToolCount: compareMetric(baseTrace.diagnosis.failedToolCount, replayTrace.diagnosis.failedToolCount),
      approvalRequired: compareFlag(baseTrace.approvals.length > 0, replayTrace.approvals.length > 0),
      replan: compareFlag(baseTrace.replans.length > 0, replayTrace.replans.length > 0),
      memoryRecallHits: compareMetric(
        baseTrace.memoryRecall?.hitCount ?? 0,
        replayTrace.memoryRecall?.hitCount ?? 0,
      ),
      finalAnswerLength: compareMetric(baseAnswer.length, replayAnswer.length),
    },
    evaluateMetrics: compareEvaluations(baseTrace.evaluation, replayTrace.evaluation),
    toolSequence: {
      rows: diffToolSequence(baseTools, replayTools),
    },
    finalAnswerDiff: diffText(baseAnswer, replayAnswer),
    finalAnswers: {
      base: baseAnswer,
      replay: replayAnswer,
    },
    traceCompleteness: {
      base: describeTraceCompleteness(baseTrace, baseAnswer),
      replay: describeTraceCompleteness(replayTrace, replayAnswer),
    },
  }
}

function compareMetric(base: number | undefined, replay: number | undefined): ComparableMetric {
  const safeBase = base ?? 0
  const safeReplay = replay ?? 0
  return {
    base: safeBase,
    replay: safeReplay,
    delta: safeReplay - safeBase,
    changed: safeBase !== safeReplay,
  }
}

function compareFlag(base: boolean, replay: boolean): ComparableFlag {
  return {
    base,
    replay,
    changed: base !== replay,
  }
}

function compareValue(base: string, replay: string): ComparableValue {
  return {
    base,
    replay,
    changed: base !== replay,
  }
}

function flattenToolSequence(trace: ParsedTrace): ReplayToolSnapshot[] {
  return trace.steps
    .flatMap((step) => step.toolCalls)
    .filter((call) => call.toolName !== 'none' && terminalStatuses.has(call.status))
    .map((call) => ({
      toolName: call.toolName,
      status: call.status,
      stepIndex: call.stepIndex,
    }))
}

function resolvePlanStepCount(trace: ParsedTrace): number {
  return trace.plan?.stepCount ?? trace.steps.length
}

function resolveDurationMs(trace: ParsedTrace, events: TraceRawEvent[]): number {
  const emittedTimes = events
    .map((event) => event.emitted_at_unix_ms)
    .filter((value): value is number => typeof value === 'number')

  if (emittedTimes.length >= 2) {
    return Math.max(...emittedTimes) - Math.min(...emittedTimes)
  }

  const createdAt = trace.task.createdAt ? Date.parse(trace.task.createdAt) : Number.NaN
  const updatedAt = trace.task.updatedAt ? Date.parse(trace.task.updatedAt) : Number.NaN
  if (Number.isFinite(createdAt) && Number.isFinite(updatedAt) && updatedAt >= createdAt) {
    return updatedAt - createdAt
  }

  return trace.evaluation?.durationMs ?? 0
}

function compareEvaluations(
  baseEvaluation: TraceEvaluation | undefined,
  replayEvaluation: TraceEvaluation | undefined,
) {
  return {
    estimatedSuccess: compareMetric(baseEvaluation?.estimatedSuccess, replayEvaluation?.estimatedSuccess),
    objectiveCompletion: compareMetric(
      baseEvaluation?.objectiveCompletion,
      replayEvaluation?.objectiveCompletion,
    ),
    toolSuccessRate: compareMetric(baseEvaluation?.toolSuccessRate, replayEvaluation?.toolSuccessRate),
    blockedActions: compareMetric(baseEvaluation?.blockedActions, replayEvaluation?.blockedActions),
    durationMs: compareMetric(baseEvaluation?.durationMs, replayEvaluation?.durationMs),
  }
}

function diffToolSequence(base: ReplayToolSnapshot[], replay: ReplayToolSnapshot[]): ToolSequenceRow[] {
  const baseNames = base.map((item) => item.toolName)
  const replayNames = replay.map((item) => item.toolName)
  const pairs = alignByLcs(baseNames, replayNames)
  return pairs.map(([baseIndex, replayIndex]) => {
    const left = typeof baseIndex === 'number' ? base[baseIndex] : undefined
    const right = typeof replayIndex === 'number' ? replay[replayIndex] : undefined

    if (!left && right) {
      return { kind: 'added', replay: right }
    }
    if (left && !right) {
      return { kind: 'removed', base: left }
    }
    if (!left || !right) {
      return { kind: 'same' }
    }

    return {
      kind: left.status === right.status ? 'same' : 'changed',
      base: left,
      replay: right,
    }
  })
}

function alignByLcs(left: string[], right: string[]): Array<[number | undefined, number | undefined]> {
  const matrix = Array.from({ length: left.length + 1 }, () => Array<number>(right.length + 1).fill(0))
  for (let i = left.length - 1; i >= 0; i -= 1) {
    for (let j = right.length - 1; j >= 0; j -= 1) {
      matrix[i][j] =
        left[i] === right[j] ? matrix[i + 1][j + 1] + 1 : Math.max(matrix[i + 1][j], matrix[i][j + 1])
    }
  }

  const rows: Array<[number | undefined, number | undefined]> = []
  let i = 0
  let j = 0
  while (i < left.length && j < right.length) {
    if (left[i] === right[j]) {
      rows.push([i, j])
      i += 1
      j += 1
      continue
    }
    if (matrix[i + 1][j] >= matrix[i][j + 1]) {
      rows.push([i, undefined])
      i += 1
    } else {
      rows.push([undefined, j])
      j += 1
    }
  }
  while (i < left.length) {
    rows.push([i, undefined])
    i += 1
  }
  while (j < right.length) {
    rows.push([undefined, j])
    j += 1
  }
  return rows
}

function collectFinalAnswer(events: TraceRawEvent[]): string {
  return events
    .filter((event) => event.type === 'token' && typeof event.token === 'string')
    .map((event) => event.token ?? '')
    .join('')
}

function diffText(base: string, replay: string): ReplayTextDiffRow[] {
  const left = splitTextUnits(base)
  const right = splitTextUnits(replay)
  const pairs = alignByLcs(left, right)
  return pairs.map(([leftIndex, rightIndex]) => {
    const leftValue = typeof leftIndex === 'number' ? left[leftIndex] : undefined
    const rightValue = typeof rightIndex === 'number' ? right[rightIndex] : undefined
    if (leftValue !== undefined && rightValue !== undefined) {
      return { kind: 'same', text: leftValue }
    }
    if (leftValue !== undefined) {
      return { kind: 'removed', text: leftValue }
    }
    return { kind: 'added', text: rightValue ?? '' }
  })
}

function splitTextUnits(value: string): string[] {
  const normalized = value.trim()
  if (normalized === '') {
    return []
  }
  const paragraphs = normalized
    .split(/\n\s*\n/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
  if (paragraphs.length > 1) {
    return paragraphs
  }
  return normalized.split(/\n/).map((item) => item.trim())
}

function describeTraceCompleteness(trace: ParsedTrace, finalAnswer: string): TraceCompleteness {
  return {
    hasPlan: Boolean(trace.plan),
    hasMemoryRecall: Boolean(trace.memoryRecall),
    hasEvaluation: Boolean(trace.evaluation),
    hasTokenOutput: finalAnswer.length > 0,
  }
}
