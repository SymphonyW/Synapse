import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { createMemory, deleteMemory, listMemories, recallMemories } from './api'
import type {
  MemoryCopy,
  MemoryCurrentUser,
  MemoryRecallHit,
  MemoryRecord,
} from './types'
import { sortMemoriesByCreatedAt } from './utils'

const DEFAULT_LIST_LIMIT = 80
const DEFAULT_RECALL_LIMIT = 5

function parsePositiveLimit(raw: string, fallback: number, max: number): number {
  const parsed = Number.parseInt(raw.trim(), 10)
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return fallback
  }

  return Math.min(parsed, max)
}

export function useMemories(currentUser: MemoryCurrentUser, tr: MemoryCopy) {
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

  return {
    isAdmin,
    targetUserID,
    setTargetUserID,
    activeUserID,
    listLimit,
    setListLimit,
    sortedMemories,
    loadingList,
    listError,
    notice,
    expandedIDs,
    deletingID,
    recallQuery,
    setRecallQuery,
    recallLimit,
    setRecallLimit,
    recallHits,
    recallLoading,
    recallError,
    hasRecalled,
    content,
    setContent,
    summary,
    setSummary,
    importance,
    setImportance,
    sourceTaskID,
    setSourceTaskID,
    createError,
    creating,
    loadMemories,
    toggleExpanded,
    handleRecall,
    handleLoadTargetUser,
    handleCreate,
    handleDelete,
  }
}
