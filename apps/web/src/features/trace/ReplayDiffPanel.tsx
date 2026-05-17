import { useMemo } from 'react'
import { buildReplayDiff } from './traceDiff'
import { parseTrace } from './traceParser'
import type { TraceRawEvent, TraceTaskContext } from './traceTypes'

export type ReplayTaskRecord = {
  id: string
  user_id: string
  prompt: string
  status: string
  error?: string
  replay_of_task_id?: string
  metadata?: Record<string, string>
  created_at: string
  updated_at: string
}

export type ReplayComparePayload = {
  base_task: ReplayTaskRecord
  other_task: ReplayTaskRecord
  base_events: TraceRawEvent[]
  other_events: TraceRawEvent[]
  base_events_truncated?: boolean
  other_events_truncated?: boolean
}

type ReplayDiffPanelProps = {
  replays: ReplayTaskRecord[]
  replaysLoaded: boolean
  loadingReplays: boolean
  loadingCompare: boolean
  compareData: ReplayComparePayload | null
  error: string
  language: 'zh' | 'en'
  onRefreshReplays: () => void
  onCompareReplay: (taskID: string) => void
  onCloseCompare: () => void
}

export function ReplayDiffPanel({
  replays,
  replaysLoaded,
  loadingReplays,
  loadingCompare,
  compareData,
  error,
  language,
  onRefreshReplays,
  onCompareReplay,
  onCloseCompare,
}: ReplayDiffPanelProps) {
  const tr = (zh: string, en: string): string => (language === 'zh' ? zh : en)

  const diffResult = useMemo(() => {
    if (!compareData) {
      return null
    }

    const baseTrace = parseTrace(compareData.base_events, toTraceTask(compareData.base_task))
    const replayTrace = parseTrace(compareData.other_events, toTraceTask(compareData.other_task))
    return buildReplayDiff({
      baseTrace,
      replayTrace,
      baseEvents: compareData.base_events,
      replayEvents: compareData.other_events,
    })
  }, [compareData])

  return (
    <section className="replay-diff-shell">
      <div className="replay-diff-toolbar">
        <div>
          <strong>{tr('Replay 对比', 'Replay Diff')}</strong>
          <p>{tr('查看该任务派生出的 replay，并比较两次执行轨迹。', 'Inspect replay children and compare two execution traces.')}</p>
        </div>
        <button className="ghost" disabled={loadingReplays} onClick={onRefreshReplays} type="button">
          {loadingReplays ? tr('加载中...', 'Loading...') : tr('查看重放记录', 'View Replays')}
        </button>
      </div>

      {error && <p className="replay-diff-error">{error}</p>}

      {replaysLoaded && replays.length === 0 && (
        <p className="replay-diff-empty">{tr('该任务还没有 replay 记录。', 'No replay records for this task yet.')}</p>
      )}

      {replays.length > 0 && (
        <div className="replay-list">
          {replays.map((replay) => (
            <div className="replay-list-item" key={replay.id}>
              <div>
                <strong>{replay.id}</strong>
                <span>{`${replay.status} · ${formatDate(replay.created_at)}`}</span>
              </div>
              <button
                className="ghost small"
                disabled={loadingCompare}
                onClick={() => onCompareReplay(replay.id)}
                type="button"
              >
                {tr('与某次 replay 对比', 'Compare Replay')}
              </button>
            </div>
          ))}
        </div>
      )}

      {compareData && diffResult && (
        <div className="replay-diff-panel">
          <div className="replay-diff-heading">
            <div>
              <span>{tr('左侧原任务', 'Original')}</span>
              <strong>{compareData.base_task.id}</strong>
            </div>
            <div>
              <span>{tr('右侧 replay', 'Replay')}</span>
              <strong>{compareData.other_task.id}</strong>
            </div>
            <button className="ghost small" onClick={onCloseCompare} type="button">
              {tr('关闭对比', 'Close')}
            </button>
          </div>

          {(compareData.base_events_truncated ||
            compareData.other_events_truncated ||
            !diffResult.traceCompleteness.base.hasPlan ||
            !diffResult.traceCompleteness.replay.hasPlan) && (
            <p className="replay-diff-warning">
              {tr(
                '存在不完整 trace；页面仍会展示已知差异，缺失阶段按空值处理。',
                'One or both traces are incomplete; known differences are still shown and missing stages are treated as empty.',
              )}
            </p>
          )}

          <div className="replay-summary-grid">
            <ReplayMetric label={tr('状态', 'Status')} value={`${diffResult.summary.status.base} → ${diffResult.summary.status.replay}`} changed={diffResult.summary.status.changed} />
            <ReplayMetric label={tr('总耗时', 'Duration')} value={formatDelta(diffResult.summary.durationMs)} changed={diffResult.summary.durationMs.changed} />
            <ReplayMetric label={tr('计划步骤', 'Plan Steps')} value={formatPair(diffResult.summary.planStepCount)} changed={diffResult.summary.planStepCount.changed} />
            <ReplayMetric label={tr('工具调用', 'Tool Calls')} value={formatPair(diffResult.summary.toolCallCount)} changed={diffResult.summary.toolCallCount.changed} />
            <ReplayMetric label={tr('成功工具', 'Tool Success')} value={formatPair(diffResult.summary.successfulToolCount)} changed={diffResult.summary.successfulToolCount.changed} />
            <ReplayMetric label={tr('失败工具', 'Tool Failures')} value={formatPair(diffResult.summary.failedToolCount)} changed={diffResult.summary.failedToolCount.changed} />
            <ReplayMetric label="approval_required" value={formatBooleanPair(diffResult.summary.approvalRequired)} changed={diffResult.summary.approvalRequired.changed} />
            <ReplayMetric label="replan" value={formatBooleanPair(diffResult.summary.replan)} changed={diffResult.summary.replan.changed} />
            <ReplayMetric label="memory_recall" value={formatPair(diffResult.summary.memoryRecallHits)} changed={diffResult.summary.memoryRecallHits.changed} />
            <ReplayMetric label={tr('最终回答长度', 'Final Answer Length')} value={formatPair(diffResult.summary.finalAnswerLength)} changed={diffResult.summary.finalAnswerLength.changed} />
          </div>

          <details className="replay-diff-section" open>
            <summary>{tr('阶段差异', 'Stage Differences')}</summary>
            <div className="replay-stage-grid">
              <ReplayStageRow
                label="plan"
                base={diffResult.traceCompleteness.base.hasPlan}
                replay={diffResult.traceCompleteness.replay.hasPlan}
              />
              <ReplayStageRow
                label="memory_recall"
                base={diffResult.traceCompleteness.base.hasMemoryRecall}
                replay={diffResult.traceCompleteness.replay.hasMemoryRecall}
              />
              <ReplayStageRow
                label="evaluate"
                base={diffResult.traceCompleteness.base.hasEvaluation}
                replay={diffResult.traceCompleteness.replay.hasEvaluation}
              />
              <ReplayStageRow
                label="final_answer"
                base={diffResult.traceCompleteness.base.hasTokenOutput}
                replay={diffResult.traceCompleteness.replay.hasTokenOutput}
              />
            </div>
          </details>

          <details className="replay-diff-section" open>
            <summary>{tr('工具调用序列', 'Tool Sequence')}</summary>
            <div className="replay-tool-sequence">
              {diffResult.toolSequence.rows.length === 0 ? (
                <p className="replay-diff-empty">{tr('两侧都没有可比较的工具调用。', 'No comparable tool calls on either side.')}</p>
              ) : (
                diffResult.toolSequence.rows.map((row, index) => (
                  <div className={`replay-tool-row ${row.kind}`} key={`${row.kind}-${index}`}>
                    <span>{row.base ? `${row.base.toolName} · ${row.base.status}` : '—'}</span>
                    <em>{row.kind}</em>
                    <span>{row.replay ? `${row.replay.toolName} · ${row.replay.status}` : '—'}</span>
                  </div>
                ))
              )}
            </div>
          </details>

          <details className="replay-diff-section" open>
            <summary>{tr('Evaluate 指标', 'Evaluate Metrics')}</summary>
            <div className="replay-evaluate-grid">
              <ReplayMetric label="estimated_success" value={formatPair(diffResult.evaluateMetrics.estimatedSuccess)} changed={diffResult.evaluateMetrics.estimatedSuccess.changed} />
              <ReplayMetric label="objective_completion" value={formatPair(diffResult.evaluateMetrics.objectiveCompletion)} changed={diffResult.evaluateMetrics.objectiveCompletion.changed} />
              <ReplayMetric label="tool_success_rate" value={formatPair(diffResult.evaluateMetrics.toolSuccessRate)} changed={diffResult.evaluateMetrics.toolSuccessRate.changed} />
              <ReplayMetric label="blocked_actions" value={formatPair(diffResult.evaluateMetrics.blockedActions)} changed={diffResult.evaluateMetrics.blockedActions.changed} />
              <ReplayMetric label="duration_ms" value={formatPair(diffResult.evaluateMetrics.durationMs)} changed={diffResult.evaluateMetrics.durationMs.changed} />
            </div>
          </details>

          <details className="replay-diff-section">
            <summary>{tr('最终回答文本 diff', 'Final Answer Text Diff')}</summary>
            {diffResult.finalAnswerDiff.length === 0 ? (
              <p className="replay-diff-empty">{tr('两侧都没有最终回答文本。', 'No final answer text on either side.')}</p>
            ) : (
              <div className="replay-text-diff">
                {diffResult.finalAnswerDiff.map((row, index) => (
                  <p className={row.kind} key={`${row.kind}-${index}`}>
                    <span>{row.kind === 'added' ? '+' : row.kind === 'removed' ? '−' : '·'}</span>
                    {row.text}
                  </p>
                ))}
              </div>
            )}
          </details>
        </div>
      )}
    </section>
  )
}

function toTraceTask(task: ReplayTaskRecord): TraceTaskContext {
  return {
    id: task.id,
    status: task.status,
    prompt: task.prompt,
    userId: task.user_id,
    createdAt: task.created_at,
    updatedAt: task.updated_at,
    error: task.error,
  }
}

function ReplayMetric({
  label,
  value,
  changed,
}: {
  label: string
  value: string
  changed: boolean
}) {
  return (
    <div className={changed ? 'changed' : ''}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function ReplayStageRow({
  label,
  base,
  replay,
}: {
  label: string
  base: boolean
  replay: boolean
}) {
  const changed = base !== replay
  return (
    <div className={changed ? 'changed' : ''}>
      <span>{label}</span>
      <strong>{`${base ? '✓' : '—'} / ${replay ? '✓' : '—'}`}</strong>
    </div>
  )
}

function formatPair(metric: { base: number; replay: number }) {
  return `${formatNumber(metric.base)} → ${formatNumber(metric.replay)}`
}

function formatDelta(metric: { base: number; replay: number; delta: number }) {
  return `${formatNumber(metric.base)} → ${formatNumber(metric.replay)} (${metric.delta >= 0 ? '+' : ''}${formatNumber(metric.delta)})`
}

function formatBooleanPair(metric: { base: boolean; replay: boolean }) {
  return `${metric.base ? 'yes' : 'no'} → ${metric.replay ? 'yes' : 'no'}`
}

function formatNumber(value: number) {
  return Number.isInteger(value) ? String(value) : value.toFixed(2)
}

function formatDate(value: string) {
  const parsed = new Date(value)
  return Number.isNaN(parsed.valueOf()) ? value : parsed.toLocaleString()
}
