import { useMemo, useState } from 'react'
import type { TraceRawEvent } from './traceTypes'

type TraceRawJsonPanelProps = {
  events: TraceRawEvent[]
  language: 'zh' | 'en'
}

export function TraceRawJsonPanel({ events, language }: TraceRawJsonPanelProps) {
  const tr = (zh: string, en: string): string => (language === 'zh' ? zh : en)
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const rawJson = useMemo(() => JSON.stringify(events, null, 2), [events])

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(rawJson)
      setCopyState('copied')
    } catch {
      setCopyState('failed')
    }
  }

  return (
    <section className="trace-raw-json">
      <div className="trace-raw-toolbar">
        <span>{tr('原始事件 JSON', 'Raw event JSON')}</span>
        <button className="ghost small" onClick={() => void handleCopy()} type="button">
          {copyState === 'copied'
            ? tr('已复制', 'Copied')
            : copyState === 'failed'
              ? tr('复制失败', 'Copy failed')
              : tr('复制', 'Copy')}
        </button>
      </div>
      <pre>{rawJson}</pre>
    </section>
  )
}
