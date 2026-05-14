import type { MemoryCopy, MemoryLanguage, MemoryRecallHit } from './types'
import { formatMemoryTime, shortMemoryID } from './utils'

type MemoryRecallResultProps = {
  hits: MemoryRecallHit[]
  hasRecalled: boolean
  loading: boolean
  language: MemoryLanguage
}

export function MemoryRecallResult({
  hits,
  hasRecalled,
  loading,
  language,
}: MemoryRecallResultProps) {
  const tr: MemoryCopy = (zh, en) => (language === 'zh' ? zh : en)

  if (loading) {
    return <p className="empty">{tr('正在召回记忆...', 'Recalling memories...')}</p>
  }

  if (!hasRecalled) {
    return (
      <p className="empty">
        {tr(
          '输入 query 后执行召回测试，这里只展示本次 recall 命中的结果。',
          'Run a recall query to show only the memories matched by this test.',
        )}
      </p>
    )
  }

  if (hits.length === 0) {
    return <p className="empty">{tr('本次 recall 没有命中记忆。', 'No memory matched this recall.')}</p>
  }

  return (
    <ol className="memory-recall-results">
      {hits.map((hit) => (
        <li key={`${hit.record.memory_id}-${hit.score}`} className="memory-recall-hit">
          <div className="memory-recall-head">
            <strong title={hit.record.memory_id}>{shortMemoryID(hit.record.memory_id)}</strong>
            <span>{tr('分数', 'score')} {hit.score.toFixed(3)}</span>
          </div>
          <p>{hit.record.summary || hit.record.content}</p>
          <div className="memory-tags">
            {hit.matched_terms.length > 0 ? (
              hit.matched_terms.map((term) => <span key={term}>{term}</span>)
            ) : (
              <span>{tr('无关键词命中', 'no matched terms')}</span>
            )}
          </div>
          <small>{formatMemoryTime(hit.record.created_at, tr)}</small>
        </li>
      ))}
    </ol>
  )
}
