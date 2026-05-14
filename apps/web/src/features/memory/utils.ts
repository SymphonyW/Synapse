import type { MemoryCopy, MemoryRecord } from './types'

export function shortMemoryID(memoryID: string): string {
  const normalized = memoryID.trim()
  if (normalized.length <= 14) {
    return normalized || 'memory'
  }

  return `${normalized.slice(0, 8)}...${normalized.slice(-4)}`
}

export function memoryCreatedAtValue(record: MemoryRecord): number {
  const raw = record.created_at
  if (typeof raw === 'number' && Number.isFinite(raw)) {
    return raw
  }

  if (typeof raw === 'string') {
    const numeric = Number(raw.trim())
    if (Number.isFinite(numeric)) {
      return numeric
    }

    const parsed = new Date(raw).getTime()
    if (Number.isFinite(parsed)) {
      return parsed
    }
  }

  return 0
}

export function sortMemoriesByCreatedAt(records: MemoryRecord[]): MemoryRecord[] {
  return [...records].sort(
    (left, right) => memoryCreatedAtValue(right) - memoryCreatedAtValue(left),
  )
}

export function formatMemoryTime(value: number | string, tr: MemoryCopy): string {
  let timestamp = 0
  if (typeof value === 'number' && Number.isFinite(value)) {
    timestamp = value
  } else if (typeof value === 'string') {
    const numeric = Number(value.trim())
    timestamp = Number.isFinite(numeric) ? numeric : new Date(value).getTime()
  }

  if (!Number.isFinite(timestamp) || timestamp <= 0) {
    return tr('未知时间', 'unknown time')
  }

  const normalized = timestamp > 0 && timestamp < 10_000_000_000 ? timestamp * 1000 : timestamp
  const date = new Date(normalized)
  if (Number.isNaN(date.getTime())) {
    return tr('未知时间', 'unknown time')
  }

  return date.toLocaleString()
}
