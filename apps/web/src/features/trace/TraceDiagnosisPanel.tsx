import type { ParsedTrace } from './traceTypes'

type TraceDiagnosisPanelProps = {
  trace: ParsedTrace
  language: 'zh' | 'en'
}

export function TraceDiagnosisPanel({ trace, language }: TraceDiagnosisPanelProps) {
  const tr = (zh: string, en: string): string => (language === 'zh' ? zh : en)
  const diagnosis = trace.diagnosis

  return (
    <section className="trace-diagnosis">
      <div className="trace-diagnosis-grid">
        <div>
          <span>{tr('工具调用', 'tool calls')}</span>
          <strong>{diagnosis.toolCallCount}</strong>
        </div>
        <div>
          <span>{tr('成功', 'success')}</span>
          <strong>{diagnosis.successfulToolCount}</strong>
        </div>
        <div>
          <span>{tr('失败', 'failed')}</span>
          <strong>{diagnosis.failedToolCount}</strong>
        </div>
        <div>
          <span>{tr('审批暂停', 'approval pause')}</span>
          <strong>{diagnosis.hasApprovalPause ? tr('有', 'yes') : tr('无', 'no')}</strong>
        </div>
        <div>
          <span>{tr('重规划', 'replan')}</span>
          <strong>{diagnosis.hasReplan ? tr('有', 'yes') : tr('无', 'no')}</strong>
        </div>
      </div>

      {trace.evaluation && (
        <p>
          {tr('最终评估', 'final evaluation')}：
          {` ${Math.round((trace.evaluation.estimatedSuccess ?? 0) * 100)}% / `}
          {`${Math.round((trace.evaluation.objectiveCompletion ?? 0) * 100)}% / `}
          {`${Math.round((trace.evaluation.toolSuccessRate ?? 0) * 100)}%`}
        </p>
      )}

      {diagnosis.lastFailureReason && (
        <p className="trace-diagnosis-failure">
          <strong>{tr('最后失败原因', 'last failure reason')}：</strong>
          {diagnosis.lastFailureReason}
        </p>
      )}
    </section>
  )
}
