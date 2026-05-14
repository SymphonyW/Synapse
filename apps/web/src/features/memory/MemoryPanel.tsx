import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { createMemory, deleteMemory, listMemories, recallMemories } from './api'
import { MemoryRecallResult } from './MemoryRecallResult'
import { MemoryRecordCard } from './MemoryRecordCard'
import type { MemoryCopy, MemoryCurrentUser, MemoryLanguage, MemoryRecallHit, MemoryRecord } from './types'
import { sortMemoriesByCreatedAt } from './utils'

type MemoryPanelProps = {
  currentUser: MemoryCurrentUser
  language: MemoryLanguage
}

const DEFAULT_LIST_LIMIT = 80
const DEFAULT_RECALL_LIMIT = 5

function parsePositiveLimit(raw: string, fallback: number, max: number): number {
  const parsed = Number.parseInt(raw.trim(), 10)
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return fallback
  }

  return Math.min(parsed, max)
}

export function MemoryPanel({ currentUser, language }: MemoryPanelProps) {
  const tr = useCallback<MemoryCopy>((zh, en) => (language === 'zh' ? zh : en), [language])
  const isAdmin = currentUser.role === 'admin'
  const [targetUserID, setTargetUserID] = useState(currentUser.username)
  const [activeUserID, setActiveUserID] = useState(currentUser.username)
  const [listLimit, setListLimit] = useState(String(DEFAULT_LIST_LIMIT))
  const [memories, setMemories] = useState<MemoryRecord[]>([])
  const [loadingList, setLoadingList] = useState(false)
  const [listError, setListError] = useState('')
  const [notice, setNotice] = useState('')
  const [expandedIDs, setExpandedIDs] = useState<string[]>([])
  const [deletingID, setDeletingID] = useState('')

  const [recallQuery, setRecallQuery] = useState('')
  const [recallLimit, setRecallLimit] = useState(String(DEFAULT_RECALL_LIMIT))
  const [recallHits, setRecallHits] = useState<MemoryRecallHit[]>([])
  const [recallLoading, setRecallLoading] = useState(false)
  const [recallError, setRecallError] = useState('')
  const [hasRecalled, setHasRecalled] = useState(false)

  const [content, setContent] = useState('')
  const [summary, setSummary] = useState('')
  const [importance, setImportance] = useState('0.6')
  const [sourceTaskID, setSourceTaskID] = useState('')
  const [createError, setCreateError] = useState('')
  const [creating, setCreating] = useState(false)

  const effectiveUserID = isAdmin ? activeUserID.trim() : undefined
  const sortedMemories = useMemo(() => sortMemoriesByCreatedAt(memories), [memories])

  const loadMemories = useCallback(async () => {
    setLoadingList(true)
    setListError('')
    setNotice('')

    try {
      const payload = await listMemories({
        limit: parsePositiveLimit(listLimit, DEFAULT_LIST_LIMIT, 200),
        userId: effectiveUserID,
      })
      setMemories(payload.items)
    } catch (error) {
      setListError(error instanceof Error ? error.message : tr('加载记忆失败', 'Failed to load memories'))
    } finally {
      setLoadingList(false)
    }
  }, [effectiveUserID, listLimit, tr])

  useEffect(() => {
    setTargetUserID(currentUser.username)
    setActiveUserID(currentUser.username)
  }, [currentUser.username])

  useEffect(() => {
    void loadMemories()
  }, [loadMemories])

  const toggleExpanded = (memoryID: string) => {
    setExpandedIDs((previous) =>
      previous.includes(memoryID)
        ? previous.filter((item) => item !== memoryID)
        : [...previous, memoryID],
    )
  }

  const handleRecall = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const query = recallQuery.trim()
    if (query === '') {
      setRecallError(tr('请输入 recall query。', 'Enter a recall query.'))
      return
    }

    setRecallLoading(true)
    setRecallError('')
    setHasRecalled(true)

    try {
      const payload = await recallMemories(
        query,
        parsePositiveLimit(recallLimit, DEFAULT_RECALL_LIMIT, 50),
        effectiveUserID,
      )
      setRecallHits(payload.items)
    } catch (error) {
      setRecallError(error instanceof Error ? error.message : tr('召回失败', 'Failed to recall memories'))
    } finally {
      setRecallLoading(false)
    }
  }

  const handleLoadTargetUser = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const nextUserID = targetUserID.trim() || currentUser.username
    if (nextUserID === activeUserID.trim()) {
      void loadMemories()
      return
    }

    setActiveUserID(nextUserID)
  }

  const handleCreate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const normalizedContent = content.trim()
    const normalizedSummary = summary.trim()
    if (normalizedContent === '' && normalizedSummary === '') {
      setCreateError(tr('content 和 summary 至少填写一项。', 'Fill in content or summary.'))
      return
    }

    const parsedImportance = Number.parseFloat(importance)
    if (!Number.isFinite(parsedImportance) || parsedImportance < 0 || parsedImportance > 1) {
      setCreateError(tr('importance 需要在 0 到 1 之间。', 'Importance must be between 0 and 1.'))
      return
    }

    setCreating(true)
    setCreateError('')
    setNotice('')

    try {
      await createMemory({
        content: normalizedContent,
        summary: normalizedSummary,
        importance: parsedImportance,
        source_task_id: sourceTaskID.trim() || undefined,
        user_id: effectiveUserID,
      })
      setContent('')
      setSummary('')
      setSourceTaskID('')
      setImportance('0.6')
      setNotice(tr('记忆已写入。', 'Memory written.'))
      await loadMemories()
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : tr('写入记忆失败', 'Failed to write memory'))
    } finally {
      setCreating(false)
    }
  }

  const handleDelete = async (record: MemoryRecord) => {
    const confirmed = window.confirm(
      tr(
        `确认删除记忆 ${record.memory_id}？此操作不可恢复。`,
        `Delete memory ${record.memory_id}? This cannot be undone.`,
      ),
    )
    if (!confirmed) {
      return
    }

    setDeletingID(record.memory_id)
    setListError('')
    setNotice('')

    try {
      await deleteMemory(record.memory_id, effectiveUserID)
      setMemories((previous) => previous.filter((item) => item.memory_id !== record.memory_id))
      setRecallHits((previous) => previous.filter((item) => item.record.memory_id !== record.memory_id))
      setExpandedIDs((previous) => previous.filter((item) => item !== record.memory_id))
      setNotice(tr('记忆已删除。', 'Memory deleted.'))
    } catch (error) {
      setListError(error instanceof Error ? error.message : tr('删除记忆失败', 'Failed to delete memory'))
    } finally {
      setDeletingID('')
    }
  }

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

        {isAdmin && (
          <form
            className="memory-target-form"
            onSubmit={handleLoadTargetUser}
          >
            <label>
              {tr('目标 user_id', 'Target user_id')}
              <input
                onChange={(event) => setTargetUserID(event.target.value)}
                placeholder={currentUser.username}
                value={targetUserID}
              />
            </label>
            <button className="ghost" disabled={loadingList} type="submit">
              {tr('查询用户记忆', 'Load User Memories')}
            </button>
          </form>
        )}

        <form className="memory-form" onSubmit={handleCreate}>
          <h3>{tr('手工写入', 'Manual Write')}</h3>
          {createError && <p className="error-banner">{createError}</p>}
          <label>
            {tr('summary', 'summary')}
            <input
              onChange={(event) => setSummary(event.target.value)}
              placeholder={tr('一句话摘要', 'One-line summary')}
              value={summary}
            />
          </label>
          <label>
            {tr('content', 'content')}
            <textarea
              onChange={(event) => setContent(event.target.value)}
              placeholder={tr('写入长期记忆的正文', 'Long-term memory content')}
              rows={5}
              value={content}
            />
          </label>
          <div className="memory-form-grid">
            <label>
              {tr('importance', 'importance')}
              <input
                max="1"
                min="0"
                onChange={(event) => setImportance(event.target.value)}
                step="0.05"
                type="number"
                value={importance}
              />
            </label>
            <label>
              {tr('source_task_id（可选）', 'source_task_id optional')}
              <input
                onChange={(event) => setSourceTaskID(event.target.value)}
                placeholder="task-..."
                value={sourceTaskID}
              />
            </label>
          </div>
          <button disabled={creating} type="submit">
            {creating ? tr('写入中...', 'Writing...') : tr('写入记忆', 'Write Memory')}
          </button>
        </form>

        <form className="memory-form" onSubmit={handleRecall}>
          <h3>{tr('召回测试', 'Recall Test')}</h3>
          {recallError && <p className="error-banner">{recallError}</p>}
          <label>
            {tr('query', 'query')}
            <input
              onChange={(event) => setRecallQuery(event.target.value)}
              placeholder={tr('输入要检索的上下文', 'Search memory context')}
              value={recallQuery}
            />
          </label>
          <label>
            {tr('limit', 'limit')}
            <input
              max="50"
              min="1"
              onChange={(event) => setRecallLimit(event.target.value)}
              type="number"
              value={recallLimit}
            />
          </label>
          <button disabled={recallLoading} type="submit">
            {recallLoading ? tr('召回中...', 'Recalling...') : tr('执行 recall', 'Run Recall')}
          </button>
        </form>
      </section>

      <section className="panel memory-list-panel">
        <div className="memory-section-head">
          <div>
            <h2>{tr('长期记忆列表', 'Long-term Memories')}</h2>
            <p>
              {isAdmin && activeUserID.trim() !== ''
                ? tr(`当前目标用户：${activeUserID.trim()}`, `Target user: ${activeUserID.trim()}`)
                : tr('当前登录用户的记忆', 'Memories for the signed-in user')}
            </p>
          </div>
          <div className="memory-list-actions">
            <label>
              {tr('数量', 'limit')}
              <input
                max="200"
                min="1"
                onChange={(event) => setListLimit(event.target.value)}
                type="number"
                value={listLimit}
              />
            </label>
            <button
              className="ghost"
              disabled={loadingList}
              onClick={() => {
                void loadMemories()
              }}
              type="button"
            >
              {loadingList ? tr('刷新中...', 'Refreshing...') : tr('刷新', 'Refresh')}
            </button>
          </div>
        </div>

        {notice && <p className="auth-notice">{notice}</p>}
        {listError && <p className="error-banner">{listError}</p>}

        <div className="memory-list" aria-live="polite">
          {loadingList && sortedMemories.length === 0 && (
            <p className="empty">{tr('正在加载长期记忆...', 'Loading long-term memories...')}</p>
          )}
          {!loadingList && sortedMemories.length === 0 && !listError && (
            <p className="empty">{tr('暂无长期记忆。', 'No long-term memories yet.')}</p>
          )}
          {sortedMemories.map((record) => (
            <MemoryRecordCard
              deleting={deletingID === record.memory_id}
              expanded={expandedIDs.includes(record.memory_id)}
              key={record.memory_id}
              language={language}
              onDelete={handleDelete}
              onToggleExpanded={toggleExpanded}
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
          {hasRecalled && <span className="memory-count">{recallHits.length}</span>}
        </div>
        <MemoryRecallResult
          hasRecalled={hasRecalled}
          hits={recallHits}
          language={language}
          loading={recallLoading}
        />
      </section>
    </main>
  )
}
