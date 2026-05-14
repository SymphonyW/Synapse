export type MemoryLanguage = 'zh' | 'en'

export type MemoryUserRole = 'admin' | 'user'

export type MemoryCurrentUser = {
  username: string
  role: MemoryUserRole
}

export type MemoryRecord = {
  memory_id: string
  user_id: string
  content: string
  summary: string
  source_task_id: string
  importance: number
  created_at: number | string
}

export type MemoryRecallHit = {
  record: MemoryRecord
  score: number
  matched_terms: string[]
}

export type MemoryListParams = {
  limit?: number
  userId?: string
}

export type CreateMemoryPayload = {
  content: string
  summary: string
  importance: number
  source_task_id?: string
  user_id?: string
}

export type MemoryCopy = (zh: string, en: string) => string
