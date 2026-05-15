import { useMemo, useState } from 'react'
import { createTraceExportSummary, parseTrace } from './traceParser'
import { TraceDiagnosisPanel } from './TraceDiagnosisPanel'
import { TraceEvaluationPanel } from './TraceEvaluationPanel'
import { TraceRawJsonPanel } from './TraceRawJsonPanel'
import { TraceStageCard } from './TraceStageCard'
import { TraceTimeline } from './TraceTimeline'
import { TraceToolCallCard } from './TraceToolCallCard'
import type { ParsedTrace, TraceRawEvent, TraceTaskContext } from './traceTypes'

type TraceWorkbenchProps = {
  task: TraceTaskContext
  events: TraceRawEvent[]
  language: 'zh' | 'en'
}

type TraceViewMode = 'structured' | 'raw'

function downloadJson(filename: string, payload: unknown) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

export function TraceWorkbench({ task, events, language }: TraceWorkbenchProps) {
  const tr = (zh: string, en: string): string => (language === 'zh' ? zh : en)
  const [viewMode, setViewMode] = useState<TraceViewMode>('structured')

  const parseResult = useMemo(() => {
    try {
      return {
        trace: parseTrace(events, task),
        error: '',
      }
    } catch (error) {
      return {
        trace: null,
        error:
          error instanceof Error
            ? error.message
            : language === 'zh'
              ? '未知解析错误'
              : 'Unknown parse error',
      }
    }
  }, [events, task, language])

  const handleExport = () => {
    const payload = {
      task_id: task.id,
      conversation_id: task.conversationId,
      status: task.status,
      raw_events: events,
      parsed_trace_summary: parseResult.trace ? createTraceExportSummary(parseResult.trace) : null,
      exported_at: new Date().toISOString(),
    }
    downloadJson(`synapse-trace-task-${task.id}.json`, payload)
  }

  return (
    <section className="trace-workbench">
      <div className="trace-workbench-toolbar">
        <div className="trace-view-switch" role="tablist" aria-label="Trace view">
          <button
            className={viewMode === 'structured' ? 'active' : ''}
            onClick={() => setViewMode('structured')}
            type="button"
          >
            {tr('结构化 Trace', 'Structured Trace')}
          </button>
          <button
            className={viewMode === 'raw' ? 'active' : ''}
            onClick={() => setViewMode('raw')}
            type="button"
          >
            {tr('原始事件 JSON', 'Raw Event JSON')}
          </button>
        </div>
        <button className="ghost" onClick={handleExport} type="button">
          {tr('导出 JSON', 'Export JSON')}
        </button>
      </div>

      {viewMode === 'raw' ? (
        <TraceRawJsonPanel events={events} language={language} />
      ) : parseResult.trace ? (
        <StructuredTraceView trace={parseResult.trace} language={language} />
      ) : (
        <div className="trace-parse-fallback">
          <p>
            {tr('Trace 解析失败，已保留原始事件供排查。', 'Trace parsing failed; raw events are still available.')}
          </p>
          {parseResult.error && <code>{parseResult.error}</code>}
          <TraceRawJsonPanel events={events} language={language} />
        </div>
      )}
    </section>
  )
}

function StructuredTraceView({
  trace,
  language,
}: {
  trace: ParsedTrace
  language: 'zh' | 'en'
}) {
  const tr = (zh: string, en: string): string => (language === 'zh' ? zh : en)

  return (
    <div className="trace-structured-view">
      <div className="trace-task-meta">
        <div>
          <span>task_id</span>
          <strong>{trace.task.id}</strong>
        </div>
        {trace.task.conversationId && (
          <div>
            <span>conversation_id</span>
            <strong>{trace.task.conversationId}</strong>
          </div>
        )}
        <div>
          <span>status</span>
          <strong>{trace.task.status}</strong>
        </div>
      </div>

      <TraceDiagnosisPanel trace={trace} language={language} />
      <TraceTimeline stages={trace.stages} />

      {trace.perceive && (
        <TraceStageCard id="perceive" title={tr('感知', 'Perceive')}>
          <div className="trace-inline-metrics">
            <span>{`${tr('上下文片段', 'short context')}: ${trace.perceive.shortContextCount ?? 0}`}</span>
            <span>{`${tr('召回记忆', 'recalled memories')}: ${trace.perceive.recalledMemoryCount ?? 0}`}</span>
          </div>
        </TraceStageCard>
      )}

      <TraceStageCard
        id="memory-recall"
        title={tr('记忆召回', 'Memory Recall')}
        subtitle={`${trace.memoryRecall?.hitCount ?? 0} ${tr('条命中', 'hits')}`}
        tone={(trace.memoryRecall?.hitCount ?? 0) > 0 ? 'ok' : 'neutral'}
      >
        {trace.memoryRecall?.hits.length ? (
          <ul className="trace-memory-list">
            {trace.memoryRecall.hits.map((hit, index) => (
              <li key={hit.memoryId ?? `memory-${index}`}>
                <strong>{hit.summary || hit.contentPreview || tr('未命名记忆', 'Untitled memory')}</strong>
                <span>{typeof hit.score === 'number' ? hit.score : '—'}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="trace-empty">{tr('本次没有长期记忆命中。', 'No long-term memory hit.')}</p>
        )}
      </TraceStageCard>

      <TraceStageCard
        id="plan"
        title={tr('规划', 'Plan')}
        subtitle={`${trace.plan?.stepCount ?? trace.steps.length} ${tr('步', 'steps')}`}
      >
        {trace.plan?.steps.length ? (
          <ol className="trace-plan-list">
            {trace.plan.steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        ) : (
          <p className="trace-empty">{tr('暂无规划事件。', 'No plan event yet.')}</p>
        )}
      </TraceStageCard>

      {trace.steps.map((step) => (
        <TraceStageCard
          id={`step-${step.index}`}
          key={step.index}
          title={`Step ${step.index}`}
          subtitle={step.objective}
          tone={
            step.toolCalls.some((call) => call.status === 'failed')
              ? 'error'
              : step.approvals.length > 0 || step.replans.length > 0
                ? 'warning'
                : 'neutral'
          }
          defaultOpen={step.approvals.length > 0 || step.replans.length > 0 || step.toolCalls.some((call) => call.status === 'failed')}
        >
          {step.approvals.map((approval) => (
            <div className="trace-highlight trace-approval" key={approval.id}>
              <strong>{tr('审批节点', 'Approval')}</strong>
              <span>{approval.toolName}</span>
              {approval.reason && <p>{approval.reason}</p>}
            </div>
          ))}

          {step.replans.map((replan) => (
            <div className="trace-highlight trace-replan" key={replan.id}>
              <strong>{tr('重规划', 'Replan')}</strong>
              <span>{replan.reason}</span>
              {(replan.fromTool || replan.toTool) && (
                <p>{`${replan.fromTool ?? '—'} → ${replan.toTool ?? '—'}`}</p>
              )}
            </div>
          ))}

          <div className="trace-tool-grid">
            {step.toolCalls.map((call) => (
              <TraceToolCallCard call={call} key={call.id} language={language} />
            ))}
          </div>

          {step.observations.length > 0 && (
            <div className="trace-note-list">
              <strong>{tr('观察', 'Observe')}</strong>
              {step.observations.map((observation) => (
                <p key={observation.id}>{observation.observation || observation.reason || '—'}</p>
              ))}
            </div>
          )}

          {step.reflections.length > 0 && (
            <div className="trace-note-list">
              <strong>{tr('反思', 'Reflect')}</strong>
              {step.reflections.map((reflection) => (
                <p key={reflection.id}>{reflection.reflection || '—'}</p>
              ))}
            </div>
          )}

          {step.toolCalls.length === 0 && step.observations.length === 0 && step.reflections.length === 0 && (
            <p className="trace-empty">{tr('该步骤暂无更多事件。', 'No additional events for this step yet.')}</p>
          )}
        </TraceStageCard>
      ))}

      <TraceStageCard
        id="synthesis-mode"
        title={tr('综合输出', 'Synthesis')}
        subtitle={trace.synthesisModes.at(-1)?.mode}
      >
        <p className="trace-empty">
          {trace.synthesisModes.at(-1)?.mode
            ? `${tr('当前模式', 'Current mode')}: ${trace.synthesisModes.at(-1)?.mode}`
            : tr('暂无综合输出模式事件。', 'No synthesis mode event yet.')}
        </p>
      </TraceStageCard>

      <TraceStageCard
        id="memory-write"
        title={tr('记忆写入', 'Memory Write')}
        subtitle={`${trace.memoryWrites.length} ${tr('次', 'writes')}`}
      >
        {trace.memoryWrites.length > 0 ? (
          <ul className="trace-memory-list">
            {trace.memoryWrites.map((memoryWrite) => (
              <li key={memoryWrite.id}>
                <strong>{memoryWrite.summary || tr('未命名记忆', 'Untitled memory')}</strong>
                <span>{memoryWrite.memoryId ?? '—'}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="trace-empty">{tr('暂无记忆写入事件。', 'No memory write event yet.')}</p>
        )}
      </TraceStageCard>

      <TraceStageCard id="evaluate" title={tr('评估', 'Evaluate')}>
        <TraceEvaluationPanel evaluation={trace.evaluation} language={language} />
      </TraceStageCard>

      {trace.parseErrors.length > 0 && (
        <TraceStageCard id="parse-warnings" title={tr('解析告警', 'Parse warnings')} tone="warning">
          <ul className="trace-warning-list">
            {trace.parseErrors.map((error) => (
              <li key={error}>{error}</li>
            ))}
          </ul>
        </TraceStageCard>
      )}
    </div>
  )
}
