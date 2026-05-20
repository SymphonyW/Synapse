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
})
