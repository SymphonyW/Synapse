import { useCallback } from 'react'
import { MemoryRecallResult } from './MemoryRecallResult'
import { MemoryRecordCard } from './MemoryRecordCard'
import type { MemoryCopy, MemoryCurrentUser, MemoryLanguage } from './types'
import { useMemories } from './useMemories'

type MemoryPanelProps = {
  currentUser: MemoryCurrentUser
  language: MemoryLanguage
}

export function MemoryPanel({ currentUser, language }: MemoryPanelProps) {
  const tr = useCallback<MemoryCopy>((zh, en) => (language === 'zh' ? zh : en), [language])
  const memory = useMemories(currentUser, tr)

  return (
    <main className="memory-layout">
      <section className="panel memory-control-panel">
        <div className="memory-section-head">
          <div>
            <h2>{tr('记忆操作', 'Memory Actions')}</h2>
            <p>
              {tr(
                '手工写入记忆，并用 recall 测试检查 Agent 将看到的上下文。',
                'Write memories manually and test what the agent can recall.',
              )}
            </p>
          </div>
        </div>

        {memory.isAdmin && (
          <form
            className="memory-target-form"
            onSubmit={memory.handleLoadTargetUser}
          >
            <label>
              {tr('目标 user_id', 'Target user_id')}
              <input
                onChange={(event) => memory.setTargetUserID(event.target.value)}
                placeholder={currentUser.username}
                value={memory.targetUserID}
              />
            </label>
            <button className="ghost" disabled={memory.loadingList} type="submit">
              {tr('查询用户记忆', 'Load User Memories')}
            </button>
          </form>
        )}

        <form className="memory-form" onSubmit={memory.handleCreate}>
          <h3>{tr('手工写入', 'Manual Write')}</h3>
          {memory.createError && <p className="error-banner">{memory.createError}</p>}
          <label>
            {tr('summary', 'summary')}
            <input
              onChange={(event) => memory.setSummary(event.target.value)}
              placeholder={tr('一句话摘要', 'One-line summary')}
              value={memory.summary}
            />
          </label>
          <label>
            {tr('content', 'content')}
            <textarea
              onChange={(event) => memory.setContent(event.target.value)}
              placeholder={tr('写入长期记忆的正文', 'Long-term memory content')}
              rows={5}
              value={memory.content}
            />
          </label>
          <div className="memory-form-grid">
            <label>
              {tr('importance', 'importance')}
              <input
                max="1"
                min="0"
                onChange={(event) => memory.setImportance(event.target.value)}
                step="0.05"
                type="number"
                value={memory.importance}
              />
            </label>
            <label>
              {tr('source_task_id（可选）', 'source_task_id optional')}
              <input
                onChange={(event) => memory.setSourceTaskID(event.target.value)}
                placeholder="task-..."
                value={memory.sourceTaskID}
              />
            </label>
          </div>
          <button disabled={memory.creating} type="submit">
            {memory.creating ? tr('写入中...', 'Writing...') : tr('写入记忆', 'Write Memory')}
          </button>
        </form>

        <form className="memory-form" onSubmit={memory.handleRecall}>
          <h3>{tr('召回测试', 'Recall Test')}</h3>
          {memory.recallError && <p className="error-banner">{memory.recallError}</p>}
          <label>
            {tr('query', 'query')}
            <input
              onChange={(event) => memory.setRecallQuery(event.target.value)}
              placeholder={tr('输入要检索的上下文', 'Search memory context')}
              value={memory.recallQuery}
            />
          </label>
          <label>
            {tr('limit', 'limit')}
            <input
              max="50"
              min="1"
              onChange={(event) => memory.setRecallLimit(event.target.value)}
              type="number"
              value={memory.recallLimit}
            />
          </label>
          <button disabled={memory.recallLoading} type="submit">
            {memory.recallLoading ? tr('召回中...', 'Recalling...') : tr('执行 recall', 'Run Recall')}
          </button>
        </form>
      </section>

      <section className="panel memory-list-panel">
        <div className="memory-section-head">
          <div>
            <h2>{tr('长期记忆列表', 'Long-term Memories')}</h2>
            <p>
              {memory.isAdmin && memory.activeUserID.trim() !== ''
                ? tr(`当前目标用户：${memory.activeUserID.trim()}`, `Target user: ${memory.activeUserID.trim()}`)
                : tr('当前登录用户的记忆', 'Memories for the signed-in user')}
            </p>
          </div>
          <div className="memory-list-actions">
            <label>
              {tr('数量', 'limit')}
              <input
                max="200"
                min="1"
                onChange={(event) => memory.setListLimit(event.target.value)}
                type="number"
                value={memory.listLimit}
              />
            </label>
            <button
              className="ghost"
              disabled={memory.loadingList}
              onClick={() => {
                void memory.loadMemories()
              }}
              type="button"
            >
              {memory.loadingList ? tr('刷新中...', 'Refreshing...') : tr('刷新', 'Refresh')}
            </button>
          </div>
        </div>

        {memory.notice && <p className="auth-notice">{memory.notice}</p>}
        {memory.listError && <p className="error-banner">{memory.listError}</p>}

        <div className="memory-list" aria-live="polite">
          {memory.loadingList && memory.sortedMemories.length === 0 && (
            <p className="empty">{tr('正在加载长期记忆...', 'Loading long-term memories...')}</p>
          )}
          {!memory.loadingList && memory.sortedMemories.length === 0 && !memory.listError && (
            <p className="empty">{tr('暂无长期记忆。', 'No long-term memories yet.')}</p>
          )}
          {memory.sortedMemories.map((record) => (
            <MemoryRecordCard
              deleting={memory.deletingID === record.memory_id}
              expanded={memory.expandedIDs.includes(record.memory_id)}
              key={record.memory_id}
              language={language}
              onDelete={memory.handleDelete}
              onToggleExpanded={memory.toggleExpanded}
              record={record}
            />
          ))}
        </div>
      </section>

      <section className="panel memory-recall-panel">
        <div className="memory-section-head">
          <div>
            <h2>{tr('本次 recall 结果', 'Current Recall Result')}</h2>
            <p>
              {tr(
                '这里与左侧完整列表分开，便于检查 query、score 和 matched_terms。',
                'Kept separate from the full list so query score and matched_terms are clear.',
              )}
            </p>
          </div>
          {memory.hasRecalled && <span className="memory-count">{memory.recallHits.length}</span>}
        </div>
        <MemoryRecallResult
          hasRecalled={memory.hasRecalled}
          hits={memory.recallHits}
          language={language}
          loading={memory.recallLoading}
        />
      </section>
    </main>
  )
}
