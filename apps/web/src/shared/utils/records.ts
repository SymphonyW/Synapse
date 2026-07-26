import type { AgentInfoEnvelope, SourceLink, StreamEvent } from '../types/domain'

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function readRecordString(
  record: Record<string, unknown> | undefined,
  key: string,
): string {
  const value = record?.[key]
  return typeof value === 'string' ? value.trim() : ''
}

export function readRecordNumber(
  record: Record<string, unknown> | undefined,
  key: string,
): number | undefined {
  const value = record?.[key]
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }

  if (typeof value === 'string') {
    const parsed = Number.parseInt(value.trim(), 10)
    return Number.isFinite(parsed) ? parsed : undefined
  }

  return undefined
}

export function readStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return []
  }

  return value
    .filter((item): item is string => typeof item === 'string' && item.trim() !== '')
    .map((item) => item.trim())
}

export function parseAgentInfoEnvelope(message?: string): AgentInfoEnvelope | null {
  const normalized = (message ?? '').trim()
  if (normalized === '') {
    return null
  }

  try {
    const parsed = JSON.parse(normalized) as unknown
    if (!isRecord(parsed) || typeof parsed.agent_event !== 'string') {
      return null
    }

    return {
      schema: typeof parsed.schema === 'string' ? parsed.schema : undefined,
      agent_event: parsed.agent_event,
      display_message:
        typeof parsed.display_message === 'string' ? parsed.display_message : undefined,
      payload: isRecord(parsed.payload) ? parsed.payload : undefined,
    }
  } catch {
    return null
  }
}

export function parseAgentInfoEvent(event: StreamEvent): AgentInfoEnvelope | null {
  if (
    typeof event.schema_version === 'string' &&
    event.schema_version.trim() !== '' &&
    typeof event.event_name === 'string' &&
    event.event_name.trim() !== '' &&
    (event.payload === undefined || isRecord(event.payload))
  ) {
    return {
      schema: event.schema_version,
      schema_version: event.schema_version,
      agent_event: event.event_name,
      event_name: event.event_name,
      payload: event.payload,
    }
  }

  return parseAgentInfoEnvelope(event.message)
}

export function extractURLCandidates(text: string): string[] {
  const matches = text.match(/https?:\/\/[^\s"'<>),\]}]+/g) ?? []
  return matches.map((item) => item.replace(/[.,;:!?，。；：！？]+$/, ''))
}

export function collectSourceLinks(payload?: Record<string, unknown>): SourceLink[] {
  if (!payload) {
    return []
  }

  const metadata = isRecord(payload.metadata) ? payload.metadata : undefined
  const rawCandidates = [
    readRecordString(payload, 'url'),
    readRecordString(payload, 'source_url'),
    readRecordString(payload, 'final_url'),
    readRecordString(metadata, 'url'),
    readRecordString(metadata, 'source_url'),
    readRecordString(metadata, 'final_url'),
    ...readStringArray(payload.sources),
    ...readStringArray(metadata?.sources),
    ...extractURLCandidates(readRecordString(payload, 'output')),
  ].filter((item) => item !== '')

  const seen = new Set<string>()
  return rawCandidates
    .filter((url) => {
      if (seen.has(url)) {
        return false
      }
      seen.add(url)
      return true
    })
    .map((url, index) => ({
      url,
      label: `source ${index + 1}`,
    }))
}
