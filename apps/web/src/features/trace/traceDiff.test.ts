import { describe, expect, it } from 'vitest'
import { buildReplayDiff } from './traceDiff'
import { parseTrace } from './traceParser'
import type { TraceRawEvent } from './traceTypes'

const info = (
  event_id: number,
  agent_event: string,
  payload: Record<string, unknown>,
): TraceRawEvent => ({
  event_id,
  type: 'info',
  message: JSON.stringify({ agent_event, payload }),
  emitted_at_unix_ms: event_id * 100,
})

const token = (event_id: number, value: string): TraceRawEvent => ({
  event_id,
  type: 'token',
  token: value,
  emitted_at_unix_ms: event_id * 100,
})

describe('buildReplayDiff', () => {
  it('highlights structural and metric differences', () => {
    const baseEvents = [
      info(1, 'plan', { step_count: 1, steps: ['search'] }),
      info(2, 'tool_finished', { step_index: 1, tool: 'web_search', ok: true }),
      info(3, 'memory_recall', { hit_count: 1, hits: [] }),
      info(4, 'evaluate', {
        estimated_success: 0.9,
        objective_completion: 1,
        tool_success_rate: 1,
        blocked_actions: 0,
      }),
      token(5, 'hello'),
    ]
    const replayEvents = [
      info(1, 'plan', { step_count: 2, steps: ['search', 'fetch'] }),
      info(2, 'tool_failed', { step_index: 1, tool: 'web_search', ok: false }),
      info(3, 'approval_required', { step_index: 1, tool_name: 'http_api' }),
      info(4, 'replan', { step_index: 1, from_tool: 'web_search', to_tool: 'http_api' }),
      info(5, 'tool_finished', { step_index: 2, tool: 'http_api', ok: true }),
      info(6, 'memory_recall', { hit_count: 3, hits: [] }),
      info(7, 'evaluate', {
        estimated_success: 0.6,
        objective_completion: 0.7,
        tool_success_rate: 0.5,
        blocked_actions: 1,
      }),
      token(8, 'hello\nworld'),
    ]

    const baseTrace = parseTrace(baseEvents, {
      id: 'task-origin',
      status: 'completed',
      createdAt: '2026-05-17T00:00:00.000Z',
      updatedAt: '2026-05-17T00:00:01.000Z',
    })
    const replayTrace = parseTrace(replayEvents, {
      id: 'task-replay',
      status: 'paused',
      createdAt: '2026-05-17T00:00:00.000Z',
      updatedAt: '2026-05-17T00:00:03.000Z',
    })

    const diff = buildReplayDiff({
      baseTrace,
      replayTrace,
      baseEvents,
      replayEvents,
    })

    expect(diff.summary.status.changed).toBe(true)
    expect(diff.summary.planStepCount.delta).toBe(1)
    expect(diff.summary.memoryRecallHits.delta).toBe(2)
    expect(diff.summary.approvalRequired.replay).toBe(true)
    expect(diff.summary.replan.replay).toBe(true)
    expect(diff.summary.finalAnswerLength.delta).toBe(6)
    expect(
      diff.toolSequence.rows.some(
        (row) => row.kind === 'added' && row.replay?.toolName === 'http_api',
      ),
    ).toBe(true)
    expect(diff.evaluateMetrics.estimatedSuccess.changed).toBe(true)
    expect(diff.finalAnswerDiff.some((row) => row.kind === 'added')).toBe(true)
  })

  it('tolerates incomplete traces', () => {
    const baseTrace = parseTrace([], {
      id: 'task-origin',
      status: 'failed',
    })
    const replayTrace = parseTrace([token(1, 'partial')], {
      id: 'task-replay',
      status: 'running',
    })

    const diff = buildReplayDiff({
      baseTrace,
      replayTrace,
      baseEvents: [],
      replayEvents: [token(1, 'partial')],
    })

    expect(diff.summary.planStepCount.base).toBe(0)
    expect(diff.summary.toolCallCount.replay).toBe(0)
    expect(diff.summary.finalAnswerLength.replay).toBe(7)
    expect(diff.traceCompleteness.base.hasPlan).toBe(false)
    expect(diff.traceCompleteness.replay.hasEvaluation).toBe(false)
  })
})
