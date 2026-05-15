import type { TraceToolCall } from './traceTypes'

type TraceToolCallCardProps = {
  call: TraceToolCall
  language: 'zh' | 'en'
}

const statusLabel = (status: TraceToolCall['status'], language: 'zh' | 'en'): string => {
  const zh = {
    selected: '已选择',
    running: '执行中',
    finished: '成功',
    failed: '失败',
    skipped: '已跳过',
    approval_required: '待审批',
  }
  const en = {
    selected: 'selected',
    running: 'running',
    finished: 'finished',
    failed: 'failed',
    skipped: 'skipped',
    approval_required: 'approval required',
  }
  return (language === 'zh' ? zh : en)[status]
}

export function TraceToolCallCard({ call, language }: TraceToolCallCardProps) {
  const tr = (zh: string, en: string): string => (language === 'zh' ? zh : en)

  return (
    <article className={`trace-tool-call status-${call.status}`}>
      <div className="trace-tool-head">
        <strong>{call.toolName}</strong>
        <span>{statusLabel(call.status, language)}</span>
      </div>

      <div className="trace-chip-row">
        {call.riskLevel && <span>{`${tr('风险', 'risk')}: ${call.riskLevel}`}</span>}
        {typeof call.durationMs === 'number' && <span>{`${call.durationMs}ms`}</span>}
        {typeof call.ok === 'boolean' && (
          <span>{call.ok ? tr('成功', 'success') : tr('失败', 'failed')}</span>
        )}
      </div>

      {call.inputPreview && (
        <div className="trace-code-block">
          <small>{tr('输入预览', 'input preview')}</small>
          <code>{call.inputPreview}</code>
        </div>
      )}

      {call.outputPreview && (
        <div className="trace-code-block">
          <small>{tr('输出预览', 'output preview')}</small>
          <code>{call.outputPreview}</code>
        </div>
      )}

      {call.failureReason && (
        <p className="trace-failure-reason">
          <strong>{tr('失败原因', 'failure reason')}：</strong>
          {call.failureReason}
        </p>
      )}
    </article>
  )
}
