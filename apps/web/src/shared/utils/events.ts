import type { StreamEvent } from '../types/domain'

export function appendUniqueEvents(
  previous: StreamEvent[],
  incoming: StreamEvent[],
): StreamEvent[] {
  if (incoming.length === 0) {
    return previous
  }

  const seenEventIDs = new Set(
    previous
      .map((event) => event.event_id)
      .filter((eventID): eventID is number => typeof eventID === 'number'),
  )
  const next = [...previous]

  incoming.forEach((event) => {
    if (typeof event.event_id === 'number') {
      if (seenEventIDs.has(event.event_id)) {
        return
      }
      seenEventIDs.add(event.event_id)
    }
    next.push(event)
  })

  return next.slice(-240)
}

export function taskEventsForDisplay(
  eventsByTaskID: Record<string, StreamEvent[]>,
  fallbackEvents: StreamEvent[],
  taskID: string,
  selectedTaskID: string,
): StreamEvent[] {
  const storedEvents = eventsByTaskID[taskID] ?? []
  if (storedEvents.length > 0) {
    return storedEvents
  }
  return taskID === selectedTaskID ? fallbackEvents : []
}
