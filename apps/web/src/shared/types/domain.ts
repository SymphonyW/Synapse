export type Language = 'zh' | 'en'

export type ViewMode = 'client' | 'memory' | 'ops' | 'policy'

export type UserRole = 'admin' | 'user'

export type AuthMode = 'login' | 'register'

export type SessionIdentity = {
  username: string
  role: UserRole
}

export type AuthPayload = {
  user: SessionIdentity
  expires_at?: string
}

export type TaskStatus = 'queued' | 'running' | 'paused' | 'completed' | 'failed' | 'canceled'

export type Task = {
  id: string
  user_id: string
  prompt: string
  status: TaskStatus
  error?: string
  replay_of_task_id?: string
  metadata?: Record<string, string>
  created_at: string
  updated_at: string
}

export type TaskListResponse = {
  items: Task[]
  count: number
}

export type TaskReplayListResponse = {
  items: Task[]
  count: number
}

export type BatchCancelFailure = {
  task_id: string
  error: string
}

export type BatchCancelResponse = {
  requested: number
  canceled_count: number
  already_canceled_count: number
  failed_count: number
  canceled: Task[]
  failed: BatchCancelFailure[]
}

export type BatchCancelResult = {
  id: string
  generated_at_unix_ms: number
  response: BatchCancelResponse
}

export type DeleteConversationResponse = {
  conversation_id: string
  deleted_count: number
  deleted_task_ids: string[]
}

export type StreamEvent = {
  event_id?: number
  type?: string
  message?: string
  token?: string
  trace_id?: string
  emitted_at_unix_ms?: number
  status?: string
  task_id?: string
}

export type StreamState = 'idle' | 'connecting' | 'live' | 'closed'

export type HealthResponse = {
  status: string
  ai_engine?: string
  model_provider?: string
  error?: string
}

export type DeadLetterTask = {
  task_id: string
  reason: string
  attempts: number
  created_at: string
  updated_at: string
}

export type DeadLetterResponse = {
  items: DeadLetterTask[]
  count: number
}

export type ApprovedToolCallPayload = {
  tool_name: string
  tool_input: string
  risk_level: string
  reason: string
  resume_step_index: number
}

export type AgentInfoEnvelope = {
  schema?: string
  agent_event?: string
  display_message?: string
  payload?: Record<string, unknown>
}

export type SourceLink = {
  url: string
  label: string
}

export type AgentTimelineItem = {
  id: string
  kind: 'plan' | 'memory' | 'tool' | 'source' | 'approval' | 'final'
  title: string
  detail: string
  time?: number
  status?: 'neutral' | 'ok' | 'warning' | 'error'
  meta: string[]
  links: SourceLink[]
  bullets: string[]
}
