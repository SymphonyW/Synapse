import type { TraceEvaluation } from './traceTypes'

type TraceEvaluationPanelProps = {
  evaluation?: TraceEvaluation
  language: 'zh' | 'en'
}

const formatRatio = (value?: number): string =>
  typeof value === 'number' ? `${Math.round(value * 100)}%` : '—'

export function TraceEvaluationPanel({ evaluation, language }: TraceEvaluationPanelProps) {
  const tr = (zh: string, en: string): string => (language === 'zh' ? zh : en)

  if (!evaluation) {
    return <p className="trace-empty">{tr('暂无评估事件。', 'No evaluation event yet.')}</p>
  }

  return (
    <div className="trace-evaluation-grid">
      <div>
        <span>estimated_success</span>
        <strong>{formatRatio(evaluation.estimatedSuccess)}</strong>
      </div>
      <div>
        <span>objective_completion</span>
        <strong>{formatRatio(evaluation.objectiveCompletion)}</strong>
      </div>
      <div>
        <span>tool_success_rate</span>
        <strong>{formatRatio(evaluation.toolSuccessRate)}</strong>
      </div>
      <div>
        <span>blocked_actions</span>
        <strong>{evaluation.blockedActions ?? '—'}</strong>
      </div>
      <div>
        <span>duration_ms</span>
        <strong>{evaluation.durationMs ?? '—'}</strong>
      </div>
    </div>
  )
}
