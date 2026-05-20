import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Task, TaskStatus } from '../../shared/types/domain'
import { useTasks } from './useTasks'

const tr = (zh: string, en: string) => en || zh

function makeTask(status: TaskStatus): Task {
  return {
    id: 'task-1',
    user_id: 'alice',
    prompt: 'hello',
    status,
    metadata: { conversation_id: 'conv-1', user_message: 'hello' },
    created_at: '2026-05-19T00:00:00Z',
    updated_at: '2026-05-19T00:00:01Z',
  }
}

describe('useTasks terminal patching', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it.each(['completed', 'failed', 'canceled'] as const)(
    'updates a task to terminal %s immediately',
    (status) => {
      const { result } = renderHook(() => useTasks({ autoRefresh: false, tr }))

      act(() => {
        result.current.upsertTask(makeTask('running'))
      })
      act(() => {
        result.current.patchTaskStatus('task-1', status)
      })

      expect(result.current.tasks[0].status).toBe(status)
    },
  )

  it('does not let a stale non-terminal snapshot overwrite a terminal patch', () => {
    const { result } = renderHook(() => useTasks({ autoRefresh: false, tr }))

    act(() => {
      result.current.upsertTask(makeTask('running'))
    })
    act(() => {
      result.current.patchTaskStatus('task-1', 'completed')
    })
    act(() => {
      result.current.upsertTask(makeTask('running'))
    })

    expect(result.current.tasks[0].status).toBe('completed')
  })

  it('does not let a stale task list refresh overwrite a terminal patch', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify({ items: [makeTask('running')], count: 1 }), {
          headers: { 'Content-Type': 'application/json' },
          status: 200,
        }),
      ),
    )

    const { result } = renderHook(() => useTasks({ autoRefresh: false, tr }))

    act(() => {
      result.current.upsertTask(makeTask('running'))
    })
    act(() => {
      result.current.patchTaskStatus('task-1', 'completed')
    })

    await act(async () => {
      await result.current.refreshTasks()
    })

    await waitFor(() => expect(result.current.tasks[0].status).toBe('completed'))
  })
})
