import assert from 'node:assert/strict'
import test from 'node:test'
import { parseTrace } from './traceParser.ts'
import type { TraceRawEvent, TraceTaskContext } from './traceTypes.ts'

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

test('parseTrace groups plan, steps, tools, approval, replan, memory and evaluation', () => {
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

  assert.equal(trace.memoryRecall?.hitCount, 1)
  assert.equal(trace.plan?.stepCount, 1)
  assert.equal(trace.steps.length, 1)
  assert.equal(trace.steps[0]?.toolCalls.length, 2)
  assert.equal(trace.steps[0]?.toolCalls[0]?.status, 'failed')
  assert.equal(trace.steps[0]?.toolCalls[0]?.failureReason, 'network_timeout')
  assert.equal(trace.steps[0]?.toolCalls[1]?.status, 'finished')
  assert.equal(trace.approvals.length, 1)
  assert.equal(trace.replans.length, 1)
  assert.equal(trace.synthesisModes[0]?.mode, 'planner')
  assert.equal(trace.memoryWrites.length, 1)
  assert.equal(trace.evaluation?.estimatedSuccess, 0.72)
  assert.equal(trace.diagnosis.toolCallCount, 2)
  assert.equal(trace.diagnosis.failedToolCount, 1)
  assert.equal(trace.diagnosis.successfulToolCount, 1)
  assert.equal(trace.diagnosis.hasApprovalPause, true)
  assert.equal(trace.diagnosis.hasReplan, true)
})

test('parseTrace tolerates malformed, partial and missing-stage events', () => {
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

  assert.equal(trace.steps.length, 1)
  assert.equal(trace.steps[0]?.index, 3)
  assert.equal(trace.steps[0]?.toolCalls[0]?.toolName, 'calculator')
  assert.equal(trace.parseErrors.length, 1)
  assert.equal(trace.diagnosis.lastFailureReason, 'task failed unexpectedly')
})
