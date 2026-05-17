import { describe, expect, it } from 'vitest'
import { parseTrace } from './traceParser'
import type { TraceRawEvent, TraceTaskContext } from './traceTypes'

const task: TraceTaskContext = {
  id: 'task-1',
  conversationId: 'conv-1',
  status: 'failed',
  prompt: 'visit example.com then summarize',
}

const info = (
  eventID: number,
  agentEvent: string,
  payload: Record<string, unknown>,
): TraceRawEvent => ({
  event_id: eventID,
  type: 'info',
  emitted_at_unix_ms: 1_700_000_000_000 + eventID,
  message: JSON.stringify({
    schema: 'synapse.agent.info.v1',
    agent_event: agentEvent,
    payload,
  }),
})

describe('parseTrace', () => {
  it('groups plan, steps, tools, approval, replan, memory and evaluation', () => {
    const trace = parseTrace(
      [
        info(1, 'perceive', { task_id: 'task-1', recalled_memory_count: 1 }),
        info(2, 'memory_recall', { hit_count: 1, hits: [{ memory_id: 'memory-1' }] }),
        info(3, 'plan', { step_count: 1, steps: ['visit source'] }),
        info(4, 'act', { step_index: 1, objective: 'visit source' }),
        info(5, 'tool_selected', {
          step_index: 1,
          objective: 'visit source',
          tool: 'open_url',
          tool_input: 'https://example.com',
          risk_level: 'high',
        }),
        info(6, 'approval_required', {
          step_index: 1,
          objective: 'visit source',
          tool: 'open_url',
          tool_input: 'https://example.com',
          risk_level: 'high',
          approval_reason: 'high risk tool call requires approval',
        }),
        info(7, 'tool_failed', {
          step_index: 1,
          objective: 'visit source',
          tool: 'open_url',
          tool_input: 'https://example.com',
          risk_level: 'high',
          duration_ms: 15,
          ok: false,
          reason: 'network_timeout',
        }),
        info(8, 'observe', {
          step_index: 1,
          tool: 'open_url',
          status: 'failed',
          observation: 'timeout',
        }),
        info(9, 'reflect', {
          step_index: 1,
          reflection: 'Need a fallback.',
        }),
        info(10, 'replan', {
          step_index: 1,
          reason: 'network_tool_failed_use_retrieval',
          from_tool: 'open_url',
          to_tool: 'retrieval',
        }),
        info(11, 'tool_selected', {
          step_index: 1,
          objective: 'visit source',
          tool: 'retrieval',
          tool_input: 'visit source',
        }),
        info(12, 'tool_started', {
          step_index: 1,
          objective: 'visit source',
          tool: 'retrieval',
          tool_input: 'visit source',
        }),
        info(13, 'tool_finished', {
          step_index: 1,
          objective: 'visit source',
          tool: 'retrieval',
          tool_input: 'visit source',
          duration_ms: 8,
          ok: true,
        }),
        info(14, 'synthesis_mode', { mode: 'planner' }),
        info(15, 'memory_write', { memory_id: 'memory-2', summary: 'stored' }),
        info(16, 'evaluate', {
          estimated_success: 0.72,
          objective_completion: 1,
          tool_success_rate: 0.5,
          blocked_actions: 1,
          duration_ms: 66,
        }),
      ],
      task,
    )

    expect(trace.memoryRecall?.hitCount).toBe(1)
    expect(trace.plan?.stepCount).toBe(1)
    expect(trace.steps).toHaveLength(1)
    expect(trace.steps[0]?.toolCalls).toHaveLength(2)
    expect(trace.steps[0]?.toolCalls[0]?.status).toBe('failed')
    expect(trace.steps[0]?.toolCalls[0]?.failureReason).toBe('network_timeout')
    expect(trace.steps[0]?.toolCalls[1]?.status).toBe('finished')
    expect(trace.approvals).toHaveLength(1)
    expect(trace.replans).toHaveLength(1)
    expect(trace.synthesisModes[0]?.mode).toBe('planner')
    expect(trace.memoryWrites).toHaveLength(1)
    expect(trace.evaluation?.estimatedSuccess).toBe(0.72)
    expect(trace.diagnosis.toolCallCount).toBe(2)
    expect(trace.diagnosis.failedToolCount).toBe(1)
    expect(trace.diagnosis.successfulToolCount).toBe(1)
    expect(trace.diagnosis.hasApprovalPause).toBe(true)
    expect(trace.diagnosis.hasReplan).toBe(true)
  })

  it('tolerates malformed, partial and missing-stage events', () => {
    const trace = parseTrace(
      [
        { event_id: 1, type: 'info', message: '{bad json' },
        info(2, 'tool_finished', {
          step_index: 3,
          tool: 'calculator',
          duration_ms: 2,
          ok: true,
        }),
        { event_id: 3, type: 'failed', message: 'task failed unexpectedly' },
      ],
      task,
    )

    expect(trace.steps).toHaveLength(1)
    expect(trace.steps[0]?.index).toBe(3)
    expect(trace.steps[0]?.toolCalls[0]?.toolName).toBe('calculator')
    expect(trace.parseErrors).toHaveLength(1)
    expect(trace.diagnosis.lastFailureReason).toBe('task failed unexpectedly')
  })
})
