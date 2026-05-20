import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Task } from '../../shared/types/domain'
import { installEventSourceMock, EventSourceMock } from '../../test/EventSourceMock'
import { ClientChatPanel } from './ClientChatPanel'

const tr = (zh: string, en: string) => en || zh
const currentUser = { username: 'alice', role: 'user' as const }

function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: 'task-1',
    user_id: 'alice',
    prompt: 'summarize',
    status: 'running',
    metadata: {
      conversation_id: 'conv-1',
      user_message: 'summarize',
      client_view: 'chat',
    },
    created_at: '2026-05-19T00:00:00Z',
    updated_at: '2026-05-19T00:00:01Z',
    ...overrides,
  }
}

function mockJson(payload: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(payload), {
      headers: { 'Content-Type': 'application/json' },
      status,
    }),
  )
}

describe('ClientChatPanel', () => {
  beforeEach(() => {
    installEventSourceMock()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('expands and collapses advanced settings', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() =>
      mockJson({ items: [], count: 0 }),
    )

    render(<ClientChatPanel currentUser={currentUser} language="en" tr={tr} />)

    fireEvent.click(screen.getByRole('button', { name: 'Show advanced settings' }))
    expect(await screen.findByText('Enable agent planning loop')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Hide advanced settings' }))
    expect(screen.queryByText('Enable agent planning loop')).not.toBeInTheDocument()
  })

  it('does not submit approved_tools while pre-approval is off', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
      const url = String(input)
      if (url.startsWith('/v1/tasks?')) {
        return mockJson({ items: [], count: 0 })
      }
      if (url === '/v1/tasks' && init?.method === 'POST') {
        return mockJson(makeTask({ id: 'created-1', status: 'queued' }), 201)
      }
      return mockJson({})
    })

    render(<ClientChatPanel currentUser={currentUser} language="en" tr={tr} />)

    fireEvent.change(screen.getByPlaceholderText('Continue chatting in this thread...'), {
      target: { value: 'hello' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/v1/tasks',
        expect.objectContaining({ method: 'POST' }),
      )
    })
    const createCall = fetchMock.mock.calls.find(
      ([input, init]) => String(input) === '/v1/tasks' && init?.method === 'POST',
    )
    const body = JSON.parse(String(createCall?.[1]?.body)) as { metadata: Record<string, string> }
    expect(body.metadata.approval_granted).toBeUndefined()
    expect(body.metadata.approved_tools).toBeUndefined()
  })

  it('selects approved tools with risk badges and serializes them on submit', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
      const url = String(input)
      if (url === '/v1/tools/catalog') {
        return mockJson({
          items: [
            {
              name: 'summarize_page',
              description: 'Summarize a page',
              risk_level: 'high',
              requires_approval: true,
              provider_name: 'builtin',
              currently_disabled: false,
              allowed_for_role: true,
              selectable: true,
            },
            {
              name: 'calculator',
              description: 'Math',
              risk_level: 'low',
              requires_approval: false,
              provider_name: 'builtin',
              currently_disabled: false,
              allowed_for_role: true,
              selectable: true,
            },
          ],
          count: 2,
        })
      }
      if (url.startsWith('/v1/tasks?')) {
        return mockJson({ items: [], count: 0 })
      }
      if (url === '/v1/tasks' && init?.method === 'POST') {
        return mockJson(makeTask({ id: 'created-2', status: 'queued' }), 201)
      }
      return mockJson({})
    })

    render(<ClientChatPanel currentUser={currentUser} language="en" tr={tr} />)

    fireEvent.click(screen.getByRole('button', { name: 'Show advanced settings' }))
    fireEvent.click(screen.getByLabelText('Pre-approve high-risk tools'))
    expect(await screen.findByText('high')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /summarize_page/ }))
    expect(screen.getByText('Selected 1 tool')).toBeInTheDocument()

    fireEvent.change(screen.getByPlaceholderText('Continue chatting in this thread...'), {
      target: { value: 'summarize' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/v1/tasks',
        expect.objectContaining({ method: 'POST' }),
      )
    })
    const createCall = fetchMock.mock.calls.find(
      ([input, init]) => String(input) === '/v1/tasks' && init?.method === 'POST',
    )
    const body = JSON.parse(String(createCall?.[1]?.body)) as { metadata: Record<string, string> }
    expect(body.metadata.approval_granted).toBe('true')
    expect(body.metadata.approved_tools).toBe('summarize_page')
  })

  it('shows a fallback message when the tool catalog fails to load', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input)
      if (url === '/v1/tools/catalog') {
        return mockJson({ error: 'catalog unavailable' }, 502)
      }
      return mockJson({ items: [], count: 0 })
    })

    render(<ClientChatPanel currentUser={currentUser} language="en" tr={tr} />)

    fireEvent.click(screen.getByRole('button', { name: 'Show advanced settings' }))
    fireEvent.click(screen.getByLabelText('Pre-approve high-risk tools'))

    expect(await screen.findByText('catalog unavailable')).toBeInTheDocument()
  })

  it('updates running conversation status immediately when terminal completed arrives and retries stale fetches', async () => {
    const runningTask = makeTask({ status: 'running' })
    const completedTask = makeTask({
      status: 'completed',
      updated_at: '2026-05-19T00:00:02Z',
    })
    let taskDetailCalls = 0
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation((input) => {
      const url = String(input)
      if (url.startsWith('/v1/tasks?')) {
        return mockJson({ items: [runningTask], count: 1 })
      }
      if (url === '/v1/tasks/task-1') {
        taskDetailCalls += 1
        return mockJson(taskDetailCalls === 1 ? runningTask : completedTask)
      }
      return mockJson({ items: [], count: 0 })
    })

    render(<ClientChatPanel currentUser={currentUser} language="en" tr={tr} />)

    expect(await screen.findAllByText('running')).not.toHaveLength(0)
    await waitFor(() => expect(EventSourceMock.instances.length).toBeGreaterThan(0))

    act(() => {
      EventSourceMock.instances[0].emit('terminal', {
        task_id: 'task-1',
        status: 'completed',
      })
    })

    expect(await screen.findAllByText('completed')).not.toHaveLength(0)
    expect(screen.queryByText('running')).not.toBeInTheDocument()

    await waitFor(() => expect(taskDetailCalls).toBeGreaterThanOrEqual(2))
    expect(fetchMock).toHaveBeenCalledWith('/v1/tasks/task-1', expect.anything())
  })
})
