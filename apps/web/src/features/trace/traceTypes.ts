export type TraceTaskStatus =
  | 'queued'
  | 'running'
  | 'paused'
  | 'completed'
  | 'failed'
  | 'canceled'
  | string

export type TraceRawEvent = {
  event_id?: number
  type?: string
  message?: string
  token?: string
  trace_id?: string
  emitted_at_unix_ms?: number
  status?: string
  task_id?: string
}

export type TraceTaskContext = {
  id: string
  conversationId?: string
  status: TraceTaskStatus
  prompt?: string
  userId?: string
  createdAt?: string
  updatedAt?: string
  error?: string
}

export type AgentInfoEnvelope = {
  schema?: string
  agent_event?: string
  display_message?: string
  payload?: Record<string, unknown>
}

export type TraceMemoryHit = {
  memoryId?: string
  summary?: string
  contentPreview?: string
  sourceTaskId?: string
  importance?: number
  createdAt?: number
  score?: number
  matchedTerms: string[]
}

export type TraceMemoryRecall = {
  eventId?: number
  time?: number
  query?: string
  hitCount: number
  hits: TraceMemoryHit[]
}

export type TracePlan = {
  eventId?: number
  time?: number
  stepCount: number
  steps: string[]
}

export type TraceToolStatus =
  | 'selected'
  | 'running'
  | 'finished'
  | 'failed'
  | 'skipped'
  | 'approval_required'

export type TraceToolCall = {
  id: string
  stepIndex: number
  objective?: string
  toolName: string
  riskLevel?: string
  inputPreview?: string
  status: TraceToolStatus
  durationMs?: number
  ok?: boolean
  failureReason?: string
  outputPreview?: string
  selectedAt?: number
  startedAt?: number
  finishedAt?: number
  requiresApproval?: boolean
}

export type TraceApproval = {
  id: string
  stepIndex?: number
  toolName?: string
  toolInput?: string
  riskLevel?: string
  reason?: string
  resumeStepIndex?: number
  time?: number
}

export type TraceReplan = {
  id: string
  stepIndex?: number
  reason?: string
  fromTool?: string
  toTool?: string
  toToolInput?: string
  time?: number
}

export type TraceObservation = {
  id: string
  toolName?: string
  status?: string
  observation?: string
  reason?: string
  replanned?: boolean
  time?: number
}

export type TraceReflection = {
  id: string
  reflection?: string
  replanned?: boolean
  time?: number
}

export type TraceStep = {
  index: number
  objective?: string
  actedAt?: number
  toolCalls: TraceToolCall[]
  observations: TraceObservation[]
  reflections: TraceReflection[]
  replans: TraceReplan[]
  approvals: TraceApproval[]
}

export type TraceSynthesisMode = {
  id: string
  mode?: string
  time?: number
}

export type TraceMemoryWrite = {
  id: string
  memoryId?: string
  summary?: string
  contentPreview?: string
  sourceTaskId?: string
  importance?: number
  createdAt?: number
  time?: number
}

export type TraceEvaluation = {
  id: string
  estimatedSuccess?: number
  objectiveCompletion?: number
  toolSuccessRate?: number
  blockedActions?: number
  durationMs?: number
  time?: number
}

export type TraceDiagnosis = {
  toolCallCount: number
  successfulToolCount: number
  failedToolCount: number
  hasApprovalPause: boolean
  hasReplan: boolean
  blockedActions?: number
  lastFailureReason?: string
}

export type TraceStageKind =
  | 'perceive'
  | 'memory_recall'
  | 'plan'
  | 'step'
  | 'approval_required'
  | 'replan'
  | 'synthesis_mode'
  | 'memory_write'
  | 'evaluate'

export type TraceStage = {
  id: string
  kind: TraceStageKind
  title: string
  subtitle?: string
  status: 'neutral' | 'ok' | 'warning' | 'error'
  time?: number
  stepIndex?: number
}

export type ParsedTrace = {
  task: TraceTaskContext
  perceive?: {
    eventId?: number
    time?: number
    taskId?: string
    shortContextCount?: number
    recalledMemoryCount?: number
  }
  memoryRecall?: TraceMemoryRecall
  plan?: TracePlan
  steps: TraceStep[]
  approvals: TraceApproval[]
  replans: TraceReplan[]
  synthesisModes: TraceSynthesisMode[]
  memoryWrites: TraceMemoryWrite[]
  evaluation?: TraceEvaluation
  stages: TraceStage[]
  parseErrors: string[]
  diagnosis: TraceDiagnosis
}

export type TraceExportSummary = {
  stageCount: number
  stepCount: number
  memoryHitCount: number
  toolCallCount: number
  successfulToolCount: number
  failedToolCount: number
  approvalCount: number
  replanCount: number
  synthesisMode?: string
  memoryWriteCount: number
  evaluation?: Omit<TraceEvaluation, 'id' | 'time'>
}
