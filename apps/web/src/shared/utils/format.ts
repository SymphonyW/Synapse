export function normalizeUsername(value: string): string {
  return value.trim().toLowerCase()
}

export function createConversationID(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }

  return `conv-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

export function truncatePreview(text: string, limit: number): string {
  const normalized = text.trim()
  if (normalized.length <= limit) {
    return normalized
  }

  return `${normalized.slice(0, limit)}...`
}

export function formatDateTime(value?: string | number): string {
  if (!value) {
    return '-'
  }

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return '-'
  }

  return date.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

export function statusClass(status?: string): string {
  switch (status) {
    case 'running':
      return 'status status-running'
    case 'paused':
      return 'status status-paused'
    case 'completed':
      return 'status status-completed'
    case 'failed':
      return 'status status-failed'
    case 'canceled':
      return 'status status-canceled'
    default:
      return 'status status-queued'
  }
}
