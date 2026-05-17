import { useEffect, useRef, useState } from 'react'
import { STREAM_EVENT_TYPES } from '../utils/constants'
import { appendUniqueEvents } from '../utils/events'
import type { StreamEvent, StreamState, Task } from '../types/domain'

type Translate = (zh: string, en: string) => string

type UseTaskEventsOptions = {
  enabled: boolean
  selectedTaskID: string
  hydrateTasks?: Task[]
  onTerminal?: (taskID: string) => void | Promise<void>
  onError?: (message: string) => void
  tr: Translate
}

export function useTaskEvents({
  enabled,
  selectedTaskID,
  hydrateTasks = [],
  onTerminal,
  onError,
  tr,
}: UseTaskEventsOptions) {
  const [events, setEvents] = useState<StreamEvent[]>([])
  const [eventsByTaskID, setEventsByTaskID] = useState<Record<string, StreamEvent[]>>({})
  const [responseByTaskID, setResponseByTaskID] = useState<Record<string, string>>({})
  const [lastEventID, setLastEventID] = useState(0)
  const [streamState, setStreamState] = useState<StreamState>('idle')

  const eventSourceRef = useRef<EventSource | null>(null)
  const taskLastEventIDRef = useRef<Record<string, number>>({})
  const hydratingTaskIDsRef = useRef<Set<string>>(new Set())
  const hydrationSourcesRef = useRef<Map<string, EventSource>>(new Map())
  const responseByTaskIDRef = useRef<Record<string, string>>({})

  useEffect(() => {
    responseByTaskIDRef.current = responseByTaskID
  }, [responseByTaskID])

  const rememberTaskEvents = (taskID: string, incoming: StreamEvent[]) => {
    if (taskID.trim() === '' || incoming.length === 0) {
      return
    }

    setEventsByTaskID((previous) => ({
      ...previous,
      [taskID]: appendUniqueEvents(previous[taskID] ?? [], incoming),
    }))
  }

  const prepareTask = (taskID: string) => {
    taskLastEventIDRef.current[taskID] = 0
    setEventsByTaskID((previous) => ({ ...previous, [taskID]: [] }))
    setResponseByTaskID((previous) => ({ ...previous, [taskID]: '' }))
    if (selectedTaskID === taskID) {
      setEvents([])
      setLastEventID(0)
    }
  }

  const removeTasks = (taskIDs: string[]) => {
    const taskIDSet = new Set(taskIDs)
    setResponseByTaskID((previous) => {
      const next = { ...previous }
      taskIDs.forEach((taskID) => {
        delete next[taskID]
      })
      return next
    })
    setEventsByTaskID((previous) => {
      const next = { ...previous }
      taskIDs.forEach((taskID) => {
        delete next[taskID]
      })
      return next
    })
    taskIDs.forEach((taskID) => {
      delete taskLastEventIDRef.current[taskID]
      const source = hydrationSourcesRef.current.get(taskID)
      if (source) {
        source.close()
        hydrationSourcesRef.current.delete(taskID)
      }
      hydratingTaskIDsRef.current.delete(taskID)
    })

    if (selectedTaskID && taskIDSet.has(selectedTaskID)) {
      eventSourceRef.current?.close()
      eventSourceRef.current = null
      setEvents([])
      setLastEventID(0)
      setStreamState('idle')
    }
  }

  const clearAll = () => {
    eventSourceRef.current?.close()
    eventSourceRef.current = null
    hydrationSourcesRef.current.forEach((source) => {
      source.close()
    })
    hydrationSourcesRef.current.clear()
    hydratingTaskIDsRef.current.clear()
    taskLastEventIDRef.current = {}
    setEvents([])
    setEventsByTaskID({})
    setResponseByTaskID({})
    setLastEventID(0)
    setStreamState('idle')
  }

  useEffect(() => {
    if (!selectedTaskID) {
      queueMicrotask(() => {
        setEvents([])
        setLastEventID(0)
        setStreamState('idle')
      })
      return
    }

    queueMicrotask(() => {
      setEvents([])
      setLastEventID(taskLastEventIDRef.current[selectedTaskID] ?? 0)
    })
  }, [selectedTaskID])

  useEffect(() => {
    if (!enabled) {
      hydrationSourcesRef.current.forEach((source) => {
        source.close()
      })
      hydrationSourcesRef.current.clear()
      hydratingTaskIDsRef.current.clear()
      return
    }

    const completedTasks = hydrateTasks.filter(
      (task) =>
        task.status === 'completed' || task.status === 'failed' || task.status === 'canceled',
    )

    completedTasks.forEach((task) => {
      const cachedText = responseByTaskID[task.id]
      const alreadyCached = typeof cachedText === 'string' && cachedText.trim() !== ''
      const alreadyHydrating = hydratingTaskIDsRef.current.has(task.id)
      if (alreadyCached || alreadyHydrating) {
        return
      }

      hydratingTaskIDsRef.current.add(task.id)

      let responseText = ''
      let hydratedLastEventID = 0
      let hydratedEvents: StreamEvent[] = []
      let closed = false
      const source = new EventSource(`/v1/tasks/${task.id}/events?last_event_id=0`)
      hydrationSourcesRef.current.set(task.id, source)

      const closeSource = () => {
        if (closed) {
          return
        }

        closed = true
        window.clearTimeout(timeoutID)
        source.close()
        hydrationSourcesRef.current.delete(task.id)
        hydratingTaskIDsRef.current.delete(task.id)
        if (hydratedLastEventID > 0) {
          const previousCursor = taskLastEventIDRef.current[task.id] ?? 0
          taskLastEventIDRef.current[task.id] = Math.max(previousCursor, hydratedLastEventID)
        }
        if (responseText.trim() !== '') {
          setResponseByTaskID((previous) => ({
            ...previous,
            [task.id]: responseText,
          }))
        }
        rememberTaskEvents(task.id, hydratedEvents)
      }

      const timeoutID = window.setTimeout(() => {
        closeSource()
      }, 20000)

      const onEvent = (event: MessageEvent<string>) => {
        try {
          const payload = JSON.parse(event.data) as StreamEvent
          const eventType = payload.type ?? event.type

          if (typeof payload.event_id === 'number') {
            hydratedLastEventID = Math.max(hydratedLastEventID, payload.event_id)
          }

          hydratedEvents = appendUniqueEvents(hydratedEvents, [{ ...payload, type: eventType }])

          if (eventType === 'token' && payload.token) {
            responseText += payload.token
          }

          if (eventType === 'terminal') {
            closeSource()
          }
        } catch {
          closeSource()
        }
      }

      STREAM_EVENT_TYPES.forEach((eventType) => {
        source.addEventListener(eventType, onEvent as EventListener)
      })
      source.onerror = closeSource
    })
  }, [enabled, hydrateTasks, responseByTaskID])

  useEffect(
    () => () => {
      hydrationSourcesRef.current.forEach((source) => {
        source.close()
      })
      hydrationSourcesRef.current.clear()
      hydratingTaskIDsRef.current.clear()
    },
    [],
  )

  useEffect(() => {
    if (!enabled || !selectedTaskID) {
      return
    }

    const taskID = selectedTaskID
    const cachedText = responseByTaskIDRef.current[taskID]
    const shouldReplayFromStart = typeof cachedText !== 'string' || cachedText.trim() === ''
    const resumeFromEventID = shouldReplayFromStart ? 0 : (taskLastEventIDRef.current[taskID] ?? 0)
    let replayedResponse: string | null = resumeFromEventID === 0 ? '' : null

    eventSourceRef.current?.close()
    queueMicrotask(() => {
      setStreamState('connecting')
      setLastEventID(resumeFromEventID)
    })

    const source = new EventSource(`/v1/tasks/${taskID}/events?last_event_id=${resumeFromEventID}`)
    eventSourceRef.current = source
    const seenEventIDs = new Set<number>()

    const onEvent = (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data) as StreamEvent
        const eventType = payload.type ?? event.type

        if (typeof payload.event_id === 'number') {
          if (seenEventIDs.has(payload.event_id)) {
            return
          }
          seenEventIDs.add(payload.event_id)
        }

        setStreamState('live')
        const normalizedEvent: StreamEvent = { ...payload, type: eventType }
        setEvents((previous) => appendUniqueEvents(previous, [normalizedEvent]))
        rememberTaskEvents(taskID, [normalizedEvent])

        if (eventType === 'token' && payload.token) {
          if (replayedResponse !== null) {
            const rebuiltResponse = replayedResponse + payload.token
            replayedResponse = rebuiltResponse
            setResponseByTaskID((previous) => ({ ...previous, [taskID]: rebuiltResponse }))
          } else {
            setResponseByTaskID((previous) => ({
              ...previous,
              [taskID]: `${previous[taskID] ?? ''}${payload.token}`,
            }))
          }
        }

        if (typeof payload.event_id === 'number') {
          setLastEventID(payload.event_id)
          taskLastEventIDRef.current[taskID] = payload.event_id
        }

        if (eventType === 'terminal') {
          setStreamState('closed')
          source.close()
          void onTerminal?.(taskID)
        }
      } catch {
        onError?.(tr('解析事件流数据失败', 'Failed to parse stream event payload'))
      }
    }

    STREAM_EVENT_TYPES.forEach((eventType) => {
      source.addEventListener(eventType, onEvent as EventListener)
    })

    source.onerror = () => {
      setStreamState('closed')
      source.close()
    }

    return () => {
      source.close()
    }
  }, [enabled, onError, onTerminal, selectedTaskID, tr])

  return {
    events,
    eventsByTaskID,
    responseByTaskID,
    lastEventID,
    streamState,
    prepareTask,
    removeTasks,
    clearAll,
  }
}
