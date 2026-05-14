import type {
  CreateMemoryPayload,
  MemoryListParams,
  MemoryRecallHit,
  MemoryRecord,
} from './types'

type MemoryListResponse = {
  items: MemoryRecord[]
  count: number
}

type MemoryRecallResponse = {
  items: MemoryRecallHit[]
  count: number
}

function readErrorMessage(payload: unknown): string {
  if (typeof payload !== 'object' || payload === null || Array.isArray(payload)) {
    return ''
  }

  const record = payload as Record<string, unknown>
  const error = record.error
  if (typeof error === 'string' && error.trim() !== '') {
    return error.trim()
  }

  const message = record.message
  return typeof message === 'string' ? message.trim() : ''
}

async function requestMemoryJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    credentials: init?.credentials ?? 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  })

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const payload: unknown = await response.json()
      const parsed = readErrorMessage(payload)
      if (parsed !== '') {
        detail = parsed
      }
    } catch {
      // 保留 HTTP 状态文本，避免把 JSON 解析失败暴露成无关错误。
    }

    throw new Error(detail)
  }

  return (await response.json()) as T
}

function appendOptionalUserID(params: URLSearchParams, userId?: string): void {
  const normalized = userId?.trim()
  if (normalized) {
    params.set('user_id', normalized)
  }
}

export async function listMemories(params: MemoryListParams = {}): Promise<MemoryListResponse> {
  const query = new URLSearchParams()
  if (typeof params.limit === 'number' && Number.isFinite(params.limit)) {
    query.set('limit', String(params.limit))
  }
  appendOptionalUserID(query, params.userId)

  const suffix = query.toString()
  return requestMemoryJson<MemoryListResponse>(`/v1/memories${suffix ? `?${suffix}` : ''}`)
}

export async function recallMemories(
  queryText: string,
  limit: number,
  userId?: string,
): Promise<MemoryRecallResponse> {
  const query = new URLSearchParams()
  query.set('query', queryText)
  query.set('limit', String(limit))
  appendOptionalUserID(query, userId)

  return requestMemoryJson<MemoryRecallResponse>(`/v1/memories/recall?${query.toString()}`)
}

export async function createMemory(payload: CreateMemoryPayload): Promise<MemoryRecord> {
  return requestMemoryJson<MemoryRecord>('/v1/memories', {
    body: JSON.stringify(payload),
    method: 'POST',
  })
}

export async function deleteMemory(memoryId: string, userId?: string): Promise<void> {
  const query = new URLSearchParams()
  appendOptionalUserID(query, userId)
  const suffix = query.toString()
  await requestMemoryJson<{ deleted: boolean; memory_id: string }>(
    `/v1/memories/${encodeURIComponent(memoryId)}${suffix ? `?${suffix}` : ''}`,
    { method: 'DELETE' },
  )
}
