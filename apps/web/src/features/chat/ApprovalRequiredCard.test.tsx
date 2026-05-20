import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Task } from '../../shared/types/domain'
import { ApprovalRequiredCard } from './ApprovalRequiredCard'

const tr = (zh: string, en: string) => en || zh

function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: 'task-paused',
    user_id: 'alice',
    prompt: 'summarize this page',
    status: 'paused',
    error: 'task paused: approval required',
    metadata: {
      agent_required_tool: 'summarize_page',
      agent_required_tool_input: 'https://example.com/report',
      agent_required_tool_risk_level: 'high',
      agent_required_reason: 'high risk web fetch',
      agent_resume_step_index: '5',
    },
    created_at: '2026-05-19T00:00:00Z',
    updated_at: '2026-05-19T00:01:00Z',
    ...overrides,
  }
}

describe('ApprovalRequiredCard', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('renders the paused task approval request after a page refresh', () => {
    render(
      <ApprovalRequiredCard
        currentUsername="alice"
        onApproved={vi.fn()}
        task={makeTask()}
        tr={tr}
      />,
    )

    expect(screen.getByText('Approval Request')).toBeInTheDocument()
    expect(screen.getByText('summarize_page')).toBeInTheDocument()
    expect(screen.getByText('high')).toBeInTheDocument()
    expect(screen.getByText('https://example.com/report')).toBeInTheDocument()
    expect(screen.getByText('high risk web fetch')).toBeInTheDocument()
    expect(screen.getByText('Waiting for user approval')).toBeInTheDocument()
  })

  it('calls the approve endpoint with approved_tool_call and shows the resuming state', async () => {
    const resumedTask = makeTask({ status: 'queued' })
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(resumedTask), {
        headers: { 'Content-Type': 'application/json' },
        status: 202,
      }),
    )
    const onApproved = vi.fn()

    render(
      <ApprovalRequiredCard
        currentUsername="alice"
        onApproved={onApproved}
        task={makeTask()}
        tr={tr}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Approve And Continue' }))

    expect(screen.getByRole('button', { name: 'Approving...' })).toBeDisabled()
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        '/v1/tasks/task-paused/approve',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            requested_by: 'alice',
            reason: 'Task approved and resumed by user',
            approved_tool_call: {
              tool_name: 'summarize_page',
              tool_input: 'https://example.com/report',
              risk_level: 'high',
              reason: 'high risk web fetch',
              resume_step_index: 5,
            },
          }),
        }),
      )
    })

    expect(await screen.findByText('Approved. Task is resuming.')).toBeInTheDocument()
    expect(onApproved).toHaveBeenCalledWith(resumedTask)
  })

  it('shows an explicit error when approval fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ error: 'forbidden' }), {
        headers: { 'Content-Type': 'application/json' },
        status: 403,
        statusText: 'Forbidden',
      }),
    )

    render(
      <ApprovalRequiredCard
        currentUsername="alice"
        onApproved={vi.fn()}
        task={makeTask()}
        tr={tr}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Approve And Continue' }))

    expect(await screen.findByText('forbidden')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve And Continue' })).not.toBeDisabled()
  })
})
