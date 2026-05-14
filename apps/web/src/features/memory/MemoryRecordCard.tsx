import type { MemoryCopy, MemoryLanguage, MemoryRecord } from './types'
import { formatMemoryTime, shortMemoryID } from './utils'

type MemoryRecordCardProps = {
  record: MemoryRecord
  language: MemoryLanguage
  expanded: boolean
  deleting: boolean
  onToggleExpanded: (memoryID: string) => void
  onDelete: (record: MemoryRecord) => void
}

export function MemoryRecordCard({
  record,
  language,
  expanded,
  deleting,
  onToggleExpanded,
  onDelete,
}: MemoryRecordCardProps) {
  const tr: MemoryCopy = (zh, en) => (language === 'zh' ? zh : en)
  const content = record.content.trim()
  const summary = record.summary.trim()
  const contentPreview = content.length > 220 ? `${content.slice(0, 220)}...` : content

  return (
    <article className={expanded ? 'memory-card expanded' : 'memory-card'}>
      <div className="memory-card-head">
        <div>
          <span className="memory-id" title={record.memory_id}>
            {shortMemoryID(record.memory_id)}
          </span>
          <strong>{summary || tr('未填写摘要', 'No summary')}</strong>
        </div>
        <span className="memory-importance">
          {tr('重要度', 'importance')} {record.importance.toFixed(2)}
        </span>
      </div>

      <p className="memory-content">{expanded ? content || summary : contentPreview || summary}</p>

      <div className="memory-meta">
        <span title={record.source_task_id || undefined}>
          {tr('来源任务', 'source')}: {record.source_task_id || tr('手工写入', 'manual')}
        </span>
        <time>{formatMemoryTime(record.created_at, tr)}</time>
      </div>

      <div className="memory-card-actions">
        <button
          className="ghost small"
          disabled={content.length <= 220 && !expanded}
          onClick={() => onToggleExpanded(record.memory_id)}
          type="button"
        >
          {expanded ? tr('收起', 'Collapse') : tr('展开', 'Expand')}
        </button>
        <button
          className="danger small"
          disabled={deleting}
          onClick={() => onDelete(record)}
          type="button"
        >
          {deleting ? tr('删除中...', 'Deleting...') : tr('删除', 'Delete')}
        </button>
      </div>
    </article>
  )
}
