import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  BATCH_RESULT_HISTORY_LIMIT,
  TASK_LIMIT,
} from '../../shared/utils/constants'
import type {
  ApprovedToolCallPayload,
  BatchCancelResult,
  Task,
  TaskStatus,
} from '../../shared/types/domain'
import type { ReplayComparePayload } from '../trace/ReplayDiffPanel'
import {
  approveTask,
  cancelTask,
  cancelTasks,
  compareTaskReplay,
  createTask,
  getTask,
  listTaskReplays,
  listTasks,
  replayTask,
} from './api'

type Translate = (zh: string, en: string) => string

type UseTasksOptions = {
  autoRefresh?: boolean
  tr: Translate
}

export function isCancelableTask(task: Task): boolean {
  return task.status === 'queued' || task.status === 'running' || task.status === 'paused'
}

export function useTasks({ autoRefresh = true, tr }: UseTasksOptions) {
  const [tasks, setTasks] = useState<Task[]>([])
  const [selectedTaskID, setSelectedTaskID] = useState('')
  const [selectedTaskIDs, setSelectedTaskIDs] = useState<string[]>([])
  const [refreshingTasks, setRefreshingTasks] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [requestError, setRequestError] = useState('')
  const [taskStatusFilter, setTaskStatusFilter] = useState<'all' | TaskStatus>('all')
  const [replayingTaskID, setReplayingTaskID] = useState('')
  const [cancelingTaskID, setCancelingTaskID] = useState('')
  const [approvingTaskID, setApprovingTaskID] = useState('')
  const [bulkCanceling, setBulkCanceling] = useState(false)
  const [batchCancelHistory, setBatchCancelHistory] = useState<BatchCancelResult[]>([])
  const [expandedBatchResultIDs, setExpandedBatchResultIDs] = useState<string[]>([])
  const [copyFeedback, setCopyFeedback] = useState<{
    resultID: string
    state: 'copied' | 'failed'
  } | null>(null)
  const [taskReplays, setTaskReplays] = useState<Task[]>([])
  const [taskReplaysLoaded, setTaskReplaysLoaded] = useState(false)
  const [loadingTaskReplays, setLoadingTaskReplays] = useState(false)
  const [loadingReplayCompare, setLoadingReplayCompare] = useState(false)
  const [replayCompareData, setReplayCompareData] = useState<ReplayComparePayload | null>(null)
  const [replayCompareError, setReplayCompareError] = useState('')

  const selectedTask = useMemo(
    () => tasks.find((task) => task.id === selectedTaskID),
    [selectedTaskID, tasks],
  )

  const selectedCancelableTaskIDs = useMemo(
    () =>
      selectedTaskIDs.filter((taskID) => {
        const task = tasks.find((item) => item.id === taskID)
        return !!task && isCancelableTask(task)
      }),
    [selectedTaskIDs, tasks],
  )

  const upsertTask = useCallback((incoming: Task) => {
    setTasks((previous) => {
      const next = [...previous]
      const index = next.findIndex((item) => item.id === incoming.id)
      if (index >= 0) {
        next[index] = incoming
      } else {
        next.unshift(incoming)
      }

      next.sort(
        (left, right) =>
          new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime(),
      )
      return next
    })
  }, [])

  const refreshTasks = useCallback(async () => {
    setRefreshingTasks(true)
    try {
      const response = await listTasks(TASK_LIMIT, taskStatusFilter)
      setTasks(response.items)
      setSelectedTaskID((previous) => {
        if (response.items.length === 0) {
          return ''
        }
        if (previous && response.items.some((item) => item.id === previous)) {
          return previous
        }
        return response.items[0].id
      })
    } catch (error) {
      setRequestError(
        error instanceof Error
          ? error.message
          : tr('获取任务列表失败', 'Failed to fetch tasks'),
      )
    } finally {
      setRefreshingTasks(false)
    }
  }, [taskStatusFilter, tr])

  const fetchTask = useCallback(
    async (taskID: string) => {
      try {
        const task = await getTask(taskID)
        upsertTask(task)
      } catch (error) {
        setRequestError(
          error instanceof Error
            ? error.message
            : tr('获取任务状态失败', 'Failed to fetch task state'),
        )
      }
    },
    [tr, upsertTask],
  )

  const create = useCallback(
    async (payload: { user_id: string; prompt: string; metadata: Record<string, string> }) => {
      setSubmitting(true)
      setRequestError('')
      try {
        const created = await createTask(payload)
        upsertTask(created)
        setSelectedTaskID(created.id)
        await refreshTasks()
        return created
      } catch (error) {
        setRequestError(
          error instanceof Error ? error.message : tr('创建任务失败', 'Failed to create task'),
        )
        return null
      } finally {
        setSubmitting(false)
      }
    },
    [refreshTasks, tr, upsertTask],
  )

  const refreshTaskReplays = useCallback(
    async (taskID: string) => {
      setLoadingTaskReplays(true)
      setReplayCompareError('')
      try {
        const response = await listTaskReplays(taskID)
        setTaskReplays(response.items)
        setTaskReplaysLoaded(true)
      } catch (error) {
        setReplayCompareError(
          error instanceof Error
            ? error.message
            : tr('获取 replay 列表失败', 'Failed to fetch replay list'),
        )
      } finally {
        setLoadingTaskReplays(false)
      }
    },
    [tr],
  )

  const compareReplayTask = useCallback(
    async (taskID: string, replayTaskID: string) => {
      setLoadingReplayCompare(true)
      setReplayCompareError('')
      try {
        const response = await compareTaskReplay(taskID, replayTaskID)
        setReplayCompareData(response)
      } catch (error) {
        setReplayCompareError(
          error instanceof Error
            ? error.message
            : tr('获取 replay 对比失败', 'Failed to compare replay'),
        )
      } finally {
        setLoadingReplayCompare(false)
      }
    },
    [tr],
  )

  const replay = useCallback(
    async (taskID: string) => {
      setReplayingTaskID(taskID)
      setRequestError('')
      try {
        const replayed = await replayTask(taskID)
        upsertTask(replayed)
        setSelectedTaskID(replayed.id)
        await refreshTasks()
        return replayed
      } catch (error) {
        setRequestError(
          error instanceof Error ? error.message : tr('重放任务失败', 'Failed to replay task'),
        )
        return null
      } finally {
        setReplayingTaskID('')
      }
    },
    [refreshTasks, tr, upsertTask],
  )

  const approve = useCallback(
    async (
      taskID: string,
      payload: {
        requested_by: string
        reason: string
        approved_tools?: string[]
        approved_tool_call?: ApprovedToolCallPayload
      },
    ) => {
      setApprovingTaskID(taskID)
      setRequestError('')
      try {
        const resumed = await approveTask(taskID, payload)
        upsertTask(resumed)
        setSelectedTaskID(taskID)
        await refreshTasks()
        return resumed
      } catch (error) {
        setRequestError(
          error instanceof Error
            ? error.message
            : tr('审批恢复任务失败', 'Failed to approve and resume task'),
        )
        return null
      } finally {
        setApprovingTaskID('')
      }
    },
    [refreshTasks, tr, upsertTask],
  )

  const cancel = useCallback(
    async (taskID: string, payload: { requested_by: string; reason: string }) => {
      setCancelingTaskID(taskID)
      setRequestError('')
      try {
        const canceled = await cancelTask(taskID, payload)
        upsertTask(canceled)
        setSelectedTaskIDs((previous) => previous.filter((id) => id !== taskID))
        if (selectedTaskID === taskID) {
          await fetchTask(taskID)
        }
        await refreshTasks()
        return canceled
      } catch (error) {
        setRequestError(
          error instanceof Error ? error.message : tr('取消任务失败', 'Failed to cancel task'),
        )
        return null
      } finally {
        setCancelingTaskID('')
      }
    },
    [fetchTask, refreshTasks, selectedTaskID, tr, upsertTask],
  )

  const toggleTaskSelection = useCallback((taskID: string, checked: boolean) => {
    setSelectedTaskIDs((previous) => {
      if (checked) {
        return previous.includes(taskID) ? previous : [...previous, taskID]
      }
      return previous.filter((id) => id !== taskID)
    })
  }, [])

  const toggleSelectAllCancelable = useCallback(
    (checked: boolean) => {
      const cancelableIDs = tasks.filter(isCancelableTask).map((task) => task.id)
      setSelectedTaskIDs((previous) => {
        if (checked) {
          return Array.from(new Set([...previous, ...cancelableIDs]))
        }
        return previous.filter((id) => !cancelableIDs.includes(id))
      })
    },
    [tasks],
  )

  const batchCancel = useCallback(
    async (payload: { requested_by: string; reason: string }) => {
      if (selectedCancelableTaskIDs.length === 0) {
        return null
      }

      setBulkCanceling(true)
      setRequestError('')
      setCopyFeedback(null)

      try {
        const response = await cancelTasks({
          task_ids: selectedCancelableTaskIDs,
          ...payload,
        })

        const nextResult: BatchCancelResult = {
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          generated_at_unix_ms: Date.now(),
          response,
        }

        setBatchCancelHistory((previous) =>
          [nextResult, ...previous].slice(0, BATCH_RESULT_HISTORY_LIMIT),
        )
        setExpandedBatchResultIDs((previous) => [
          nextResult.id,
          ...previous
            .filter((resultID) => resultID !== nextResult.id)
            .slice(0, BATCH_RESULT_HISTORY_LIMIT - 1),
        ])
        setCopyFeedback((previous) => {
          if (!previous) {
            return null
          }
          const stillExists = [nextResult, ...batchCancelHistory].some(
            (item) => item.id === previous.resultID,
          )
          return stillExists ? previous : null
        })

        response.canceled.forEach(upsertTask)

        if (response.failed_count > 0) {
          const detail = response.failed
            .slice(0, 3)
            .map((item) => `${item.task_id}: ${item.error}`)
            .join('; ')
          setRequestError(
            tr(
              `批量取消部分失败（${response.failed_count} 项）：${detail}`,
              `Partial cancel failure (${response.failed_count}): ${detail}`,
            ),
          )
        }

        const failedIDs = new Set(response.failed.map((item) => item.task_id))
        setSelectedTaskIDs((previous) => previous.filter((id) => failedIDs.has(id)))
        await refreshTasks()
        return nextResult
      } catch (error) {
        setRequestError(
          error instanceof Error
            ? error.message
            : tr('批量取消失败', 'Failed to cancel selected tasks'),
        )
        return null
      } finally {
        setBulkCanceling(false)
      }
    },
    [batchCancelHistory, refreshTasks, selectedCancelableTaskIDs, tr, upsertTask],
  )

  const toggleBatchResultExpanded = useCallback((resultID: string) => {
    setExpandedBatchResultIDs((previous) =>
      previous.includes(resultID)
        ? previous.filter((id) => id !== resultID)
        : [...previous, resultID],
    )
  }, [])

  const copyFailedTaskIDs = useCallback(async (result: BatchCancelResult) => {
    if (result.response.failed.length === 0) {
      return
    }

    const content = result.response.failed.map((item) => item.task_id).join('\n')
    try {
      await navigator.clipboard.writeText(content)
      setCopyFeedback({ resultID: result.id, state: 'copied' })
    } catch {
      setCopyFeedback({ resultID: result.id, state: 'failed' })
    }

    window.setTimeout(() => {
      setCopyFeedback((previous) => {
        if (!previous || previous.resultID !== result.id) {
          return previous
        }
        return null
      })
    }, 1800)
  }, [])

  const removeTasks = useCallback((taskIDs: string[]) => {
    const taskIDSet = new Set(taskIDs)
    setTasks((previous) => previous.filter((task) => !taskIDSet.has(task.id)))
    setSelectedTaskIDs((previous) => previous.filter((taskID) => !taskIDSet.has(taskID)))
    setSelectedTaskID((previous) => (taskIDSet.has(previous) ? '' : previous))
  }, [])

  const clearAll = useCallback(() => {
    setTasks([])
    setSelectedTaskID('')
    setSelectedTaskIDs([])
    setRefreshingTasks(false)
    setSubmitting(false)
    setRequestError('')
    setTaskStatusFilter('all')
    setReplayingTaskID('')
    setCancelingTaskID('')
    setApprovingTaskID('')
    setBulkCanceling(false)
    setBatchCancelHistory([])
    setExpandedBatchResultIDs([])
    setCopyFeedback(null)
    setTaskReplays([])
    setTaskReplaysLoaded(false)
    setLoadingTaskReplays(false)
    setLoadingReplayCompare(false)
    setReplayCompareData(null)
    setReplayCompareError('')
  }, [])

  useEffect(() => {
    setSelectedTaskIDs((previous) => {
      const next = previous.filter((taskID) => tasks.some((task) => task.id === taskID))
      if (next.length === previous.length && next.every((taskID, index) => taskID === previous[index])) {
        return previous
      }
      return next
    })
  }, [tasks])

  useEffect(() => {
    setTaskReplays([])
    setTaskReplaysLoaded(false)
    setReplayCompareData(null)
    setReplayCompareError('')
  }, [selectedTaskID])

  useEffect(() => {
    if (!autoRefresh) {
      return
    }

    void refreshTasks()
    const timer = window.setInterval(() => {
      void refreshTasks()
    }, 4000)
    return () => {
      window.clearInterval(timer)
    }
  }, [autoRefresh, refreshTasks])

  useEffect(() => {
    if (!autoRefresh || !selectedTaskID) {
      return
    }

    const timer = window.setInterval(() => {
      void fetchTask(selectedTaskID)
    }, 1500)
    return () => {
      window.clearInterval(timer)
    }
  }, [autoRefresh, fetchTask, selectedTaskID])

  return {
    tasks,
    selectedTask,
    selectedTaskID,
    setSelectedTaskID,
    selectedTaskIDs,
    selectedCancelableTaskIDs,
    refreshingTasks,
    submitting,
    requestError,
    setRequestError,
    taskStatusFilter,
    setTaskStatusFilter,
    replayingTaskID,
    cancelingTaskID,
    approvingTaskID,
    bulkCanceling,
    batchCancelHistory,
    expandedBatchResultIDs,
    copyFeedback,
    taskReplays,
    taskReplaysLoaded,
    loadingTaskReplays,
    loadingReplayCompare,
    replayCompareData,
    replayCompareError,
    setReplayCompareData,
    refreshTasks,
    fetchTask,
    create,
    replay,
    approve,
    cancel,
    batchCancel,
    toggleTaskSelection,
    toggleSelectAllCancelable,
    toggleBatchResultExpanded,
    copyFailedTaskIDs,
    refreshTaskReplays,
    compareReplayTask,
    removeTasks,
    clearAll,
    upsertTask,
  }
}
