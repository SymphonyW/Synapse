import type {
  CreateMemoryPayload,
  MemoryListParams,
  MemoryRecallHit,
  MemoryRecord,
} from './types'
import { apiRequest } from '../../shared/api/client'

type MemoryListResponse = {
  items: MemoryRecord[]
  count: number
}

type MemoryRecallResponse = {
  items: MemoryRecallHit[]
  count: number
}

function appendOptionalUserID(params: URLSearchParams, userId?: string): void {
  const normalized = userId?.trim()
  if (normalized) {
    params.set('user_id', normalized)
  }
}

export async function listMemories(params: MemoryListParams = {}): Promise<MemoryListResponse> {
  return apiRequest<MemoryListResponse>(buildMemoryListPath(params))
}

export function buildMemoryListPath(params: MemoryListParams = {}): string {
  const query = new URLSearchParams()
  if (typeof params.limit === 'number' && Number.isFinite(params.limit)) {
    query.set('limit', String(params.limit))
  }
  appendOptionalUserID(query, params.userId)

  const suffix = query.toString()
  return `/v1/memories${suffix ? `?${suffix}` : ''}`
}

export async function recallMemories(
  queryText: string,
  limit: number,
  userId?: string,
): Promise<MemoryRecallResponse> {
  return apiRequest<MemoryRecallResponse>(buildRecallMemoryPath(queryText, limit, userId))
}

export function buildRecallMemoryPath(
  queryText: string,
  limit: number,
  userId?: string,
): string {
  const query = new URLSearchParams()
  query.set('query', queryText)
  query.set('limit', String(limit))
  appendOptionalUserID(query, userId)

  return `/v1/memories/recall?${query.toString()}`
}

export async function createMemory(payload: CreateMemoryPayload): Promise<MemoryRecord> {
  return apiRequest<MemoryRecord>('/v1/memories', {
    body: JSON.stringify(payload),
    method: 'POST',
  })
}

export async function deleteMemory(memoryId: string, userId?: string): Promise<void> {
  const query = new URLSearchParams()
  appendOptionalUserID(query, userId)
  const suffix = query.toString()
  await apiRequest<{ deleted: boolean; memory_id: string }>(
    `/v1/memories/${encodeURIComponent(memoryId)}${suffix ? `?${suffix}` : ''}`,
    { method: 'DELETE' },
  )
}
