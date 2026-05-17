import { apiRequest } from '../../shared/api/client'
import type {
  ApprovedToolCallPayload,
  BatchCancelResponse,
  Task,
  TaskListResponse,
  TaskReplayListResponse,
} from '../../shared/types/domain'
import type { ReplayComparePayload } from '../trace/ReplayDiffPanel'

export function listTasks(limit: number, status?: string): Promise<TaskListResponse> {
  const params = new URLSearchParams()
  params.set('limit', String(limit))
  if (status && status !== 'all') {
    params.set('status', status)
  }
  return apiRequest<TaskListResponse>(`/v1/tasks?${params.toString()}`)
}

export function getTask(taskID: string): Promise<Task> {
  return apiRequest<Task>(`/v1/tasks/${taskID}`)
}

export function createTask(payload: {
  user_id: string
  prompt: string
  metadata: Record<string, string>
}): Promise<Task> {
  return apiRequest<Task>('/v1/tasks', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function replayTask(taskID: string): Promise<Task> {
  return apiRequest<Task>(`/v1/tasks/${taskID}/replay`, {
    method: 'POST',
  })
}

export function approveTask(
  taskID: string,
  payload: {
    requested_by: string
    reason: string
    approved_tools?: string[]
    approved_tool_call?: ApprovedToolCallPayload
  },
): Promise<Task> {
  return apiRequest<Task>(`/v1/tasks/${taskID}/approve`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function cancelTask(
  taskID: string,
  payload: {
    requested_by: string
    reason: string
  },
): Promise<Task> {
  return apiRequest<Task>(`/v1/tasks/${taskID}/cancel`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function cancelTasks(payload: {
  task_ids: string[]
  requested_by: string
  reason: string
}): Promise<BatchCancelResponse> {
  return apiRequest<BatchCancelResponse>('/v1/tasks/cancel', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function listTaskReplays(taskID: string): Promise<TaskReplayListResponse> {
  return apiRequest<TaskReplayListResponse>(`/v1/tasks/${taskID}/replays`)
}

export function compareTaskReplay(
  taskID: string,
  replayTaskID: string,
): Promise<ReplayComparePayload> {
  return apiRequest<ReplayComparePayload>(`/v1/tasks/${taskID}/compare/${replayTaskID}`)
}
