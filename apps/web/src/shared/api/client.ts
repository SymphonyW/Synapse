import { resolveApiPath } from './config'

export class ApiError extends Error {
  readonly status: number
  readonly payload: unknown

  constructor(message: string, status: number, payload?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
  }
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

export async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(resolveApiPath(path), {
    ...init,
    credentials: init?.credentials ?? 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  })

  const contentType = response.headers.get('content-type') ?? ''
  const hasJsonBody = contentType.includes('application/json')
  const payload = hasJsonBody ? ((await response.json()) as unknown) : undefined

  if (!response.ok) {
    const detail =
      readErrorMessage(payload) || `${response.status} ${response.statusText}`.trim()
    throw new ApiError(detail, response.status, payload)
  }

  return payload as T
}
