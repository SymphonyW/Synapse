import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { installEventSourceMock, EventSourceMock } from '../../test/EventSourceMock'
import { useTaskEvents } from './useTaskEvents'

const tr = (zh: string, en: string) => en || zh

describe('useTaskEvents', () => {
  beforeEach(() => {
    installEventSourceMock()
  })

  it('notifies the owner with terminal completed status', async () => {
    const onTerminal = vi.fn()

    renderHook(() =>
      useTaskEvents({
        enabled: true,
        selectedTaskID: 'task-1',
        onTerminal,
        onError: vi.fn(),
        tr,
      }),
    )

    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))

    act(() => {
      EventSourceMock.instances[0].emit('terminal', {
        task_id: 'task-1',
        status: 'completed',
      })
    })

    expect(onTerminal).toHaveBeenCalledWith({
      taskID: 'task-1',
      status: 'completed',
    })
  })

  it.each(['failed', 'canceled'] as const)('notifies terminal %s status', async (status) => {
    const onTerminal = vi.fn()

    renderHook(() =>
      useTaskEvents({
        enabled: true,
        selectedTaskID: 'task-terminal',
        onTerminal,
        onError: vi.fn(),
        tr,
      }),
    )

    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))

    act(() => {
      EventSourceMock.instances[0].emit('terminal', {
        task_id: 'task-terminal',
        status,
      })
    })

    expect(onTerminal).toHaveBeenCalledWith({
      taskID: 'task-terminal',
      status,
    })
  })

  it('resets streamed assistant text when a retry attempt is replayed', async () => {
    const { result } = renderHook(() =>
      useTaskEvents({
        enabled: true,
        selectedTaskID: 'task-retry',
        onTerminal: vi.fn(),
        onError: vi.fn(),
        tr,
      }),
    )

    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))

    act(() => {
      EventSourceMock.instances[0].emit('token', {
        event_id: 1,
        token: 'old expanded failure. ',
      })
      EventSourceMock.instances[0].emit('info', {
        event_id: 2,
        message: 'retry_attempt',
      })
      EventSourceMock.instances[0].emit('token', {
        event_id: 3,
        token: 'fresh concise failure.',
      })
    })

    await waitFor(() =>
      expect(result.current.responseByTaskID['task-retry']).toBe('fresh concise failure.'),
    )
  })

  it('does not append terminal or completed messages to streamed token text', async () => {
    const { result } = renderHook(() =>
      useTaskEvents({
        enabled: true,
        selectedTaskID: 'task-final',
        onTerminal: vi.fn(),
        onError: vi.fn(),
        tr,
      }),
    )

    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))

    act(() => {
      EventSourceMock.instances[0].emit('token', {
        event_id: 1,
        token: 'final answer',
      })
      EventSourceMock.instances[0].emit('completed', {
        event_id: 2,
        message: 'final answer',
      })
      EventSourceMock.instances[0].emit('terminal', {
        task_id: 'task-final',
        status: 'completed',
      })
    })

    await waitFor(() =>
      expect(result.current.responseByTaskID['task-final']).toBe('final answer'),
    )
  })

  it('hydrates completed tasks from the latest retry attempt only', async () => {
    const { result } = renderHook(() =>
      useTaskEvents({
        enabled: true,
        selectedTaskID: '',
        hydrateTasks: [
          {
            id: 'task-hydrate',
            user_id: 'alice',
            prompt: 'call api',
            status: 'completed',
            created_at: '2026-05-20T00:00:00Z',
            updated_at: '2026-05-20T00:00:01Z',
          },
        ],
        onTerminal: vi.fn(),
        onError: vi.fn(),
        tr,
      }),
    )

    await waitFor(() => expect(EventSourceMock.instances).toHaveLength(1))

    act(() => {
      EventSourceMock.instances[0].emit('token', {
        event_id: 1,
        token: 'old failure. ',
      })
      EventSourceMock.instances[0].emit('info', {
        event_id: 2,
        message: 'retry_attempt',
      })
      EventSourceMock.instances[0].emit('token', {
        event_id: 3,
        token: 'fresh failure.',
      })
      EventSourceMock.instances[0].emit('terminal', {
        task_id: 'task-hydrate',
        status: 'completed',
      })
    })

    await waitFor(() =>
      expect(result.current.responseByTaskID['task-hydrate']).toBe('fresh failure.'),
    )
  })
})
