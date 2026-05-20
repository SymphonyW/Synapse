import { describe, expect, it } from 'vitest'
import type { Task } from '../../shared/types/domain'
import {
  buildApprovalPayloadFromTask,
  buildApprovedToolCallFromTask,
  getApprovalRequestSummary,
  isTaskApprovalRequired,
} from './approval'

const tr = (zh: string, en: string) => en || zh

function makeTask(overrides: Partial<Task> = {}): Task {
  return {
    id: 'task-1',
    user_id: 'alice',
    prompt: 'fetch this page',
    status: 'paused',
    error: 'task paused: approval required',
    metadata: {
      agent_required_tool: 'summarize_page',
      agent_required_tool_input: 'https://example.com/report',
      agent_required_tool_risk_level: 'high',
      agent_required_reason: 'high risk web fetch',
      agent_resume_step_index: '3',
    },
    created_at: '2026-05-19T00:00:00Z',
    updated_at: '2026-05-19T00:01:00Z',
    ...overrides,
  }
}

describe('task approval helpers', () => {
  it('recognizes paused tasks with required tool metadata', () => {
    expect(isTaskApprovalRequired(makeTask())).toBe(true)
    expect(isTaskApprovalRequired(makeTask({ status: 'running' }))).toBe(false)
    expect(
      isTaskApprovalRequired(makeTask({ metadata: { agent_required_tool: '   ' } })),
    ).toBe(false)
  })

  it('builds exact approved_tool_call payload from paused metadata', () => {
    expect(buildApprovedToolCallFromTask(makeTask(), tr)).toEqual({
      tool_name: 'summarize_page',
      tool_input: 'https://example.com/report',
      risk_level: 'high',
      reason: 'high risk web fetch',
      resume_step_index: 3,
    })
  })

  it('falls back to tool-name approval only when exact input metadata is unavailable', () => {
    const task = makeTask({
      metadata: {
        agent_required_tool: 'http_api',
        agent_required_tool_risk_level: 'high',
      },
    })

    expect(buildApprovedToolCallFromTask(task, tr)).toBeNull()
    expect(buildApprovalPayloadFromTask(task, 'alice', tr)).toEqual({
      requested_by: 'alice',
      reason: 'Task approved and resumed by user',
      approved_tools: ['http_api'],
    })
  })

  it('summarizes the visible approval request fields', () => {
    expect(getApprovalRequestSummary(makeTask(), tr)).toEqual({
      toolName: 'summarize_page',
      toolInput: 'https://example.com/report',
      riskLevel: 'high',
      reason: 'high risk web fetch',
      resumeStepIndex: 3,
      statusLabel: 'Waiting for user approval',
    })
  })
})
