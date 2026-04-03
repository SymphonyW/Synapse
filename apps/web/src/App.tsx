import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import './App.css'

// 控制台展示与筛选使用的任务生命周期状态。
type TaskStatus = 'queued' | 'running' | 'completed' | 'failed' | 'canceled'
type Language = 'zh' | 'en'
type ViewMode = 'client' | 'ops'

type Task = {
  id: string
  user_id: string
  prompt: string
  status: TaskStatus
  error?: string
  created_at: string
  updated_at: string
}

type HealthResponse = {
  status: string
  ai_engine?: string
  model_provider?: string
  error?: string
}

type DeadLetterTask = {
  task_id: string
  reason: string
  attempts: number
  created_at: string
  updated_at: string
}

type DeadLetterResponse = {
  items: DeadLetterTask[]
  count: number
}

type TaskListResponse = {
  items: Task[]
  count: number
}

type BatchCancelFailure = {
  task_id: string
  error: string
}

type BatchCancelResponse = {
  requested: number
  canceled_count: number
  already_canceled_count: number
  failed_count: number
  canceled: Task[]
  failed: BatchCancelFailure[]
}

type BatchCancelResult = {
  id: string
  generated_at_unix_ms: number
  response: BatchCancelResponse
}

type StreamEvent = {
  event_id?: number
  type?: string
  message?: string
  token?: string
  trace_id?: string
  emitted_at_unix_ms?: number
  status?: string
  task_id?: string
}

type StreamState = 'idle' | 'connecting' | 'live' | 'closed'

// 前端通过 EventSource 监听的服务端事件类型全集。
const STREAM_EVENT_TYPES = [
  'info',
  'started',
  'token',
  'cancel_requested',
  'canceled',
  'completed',
  'failed',
  'dead_lettered',
  'replay_requested',
  'terminal',
  'unspecified',
]

const DEAD_LETTER_LIMIT = 100
const TASK_LIMIT = 120
const BATCH_RESULT_HISTORY_LIMIT = 8
const LANGUAGE_STORAGE_KEY = 'synapse.web.language'
const VIEW_MODE_STORAGE_KEY = 'synapse.web.view-mode'

// formatDateTime 统一处理 ISO 字符串和 unix 毫秒时间戳。
function formatDateTime(value?: string | number): string {
  if (!value) {
    return '-'
  }

  const date = typeof value === 'number' ? new Date(value) : new Date(value)
  if (Number.isNaN(date.getTime())) {
    return '-'
  }

  return date.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function statusClass(status?: string): string {
  switch (status) {
    case 'running':
      return 'status status-running'
    case 'completed':
      return 'status status-completed'
    case 'failed':
      return 'status status-failed'
    case 'canceled':
      return 'status status-canceled'
    default:
      return 'status status-queued'
  }
}

// requestJson 统一封装 fetch 与错误解析，保证界面错误提示风格一致。
async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  })

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const payload = (await response.json()) as { error?: string }
      if (payload.error) {
        detail = payload.error
      }
    } catch {
      // 忽略 JSON 解析失败，保留默认状态文本。
    }
    throw new Error(detail)
  }

  return (await response.json()) as T
}

function App() {
  const [language, setLanguage] = useState<Language>(() => {
    if (typeof window === 'undefined') {
      return 'zh'
    }

    const persisted = window.localStorage.getItem(LANGUAGE_STORAGE_KEY)
    return persisted === 'en' ? 'en' : 'zh'
  })

  const tr = (zh: string, en: string): string => (language === 'zh' ? zh : en)

  const [viewMode, setViewMode] = useState<ViewMode>(() => {
    if (typeof window === 'undefined') {
      return 'client'
    }

    const persisted = window.localStorage.getItem(VIEW_MODE_STORAGE_KEY)
    return persisted === 'ops' ? 'ops' : 'client'
  })

  // 表单输入与选择态。
  const [userID, setUserID] = useState('founder')
  const [prompt, setPrompt] = useState(
    language === 'zh'
      ? '请整理一份 Synapse Alpha 测试上线检查清单。'
      : 'Draft a launch checklist for Synapse alpha testing.',
  )
  const [tasks, setTasks] = useState<Task[]>([])
  const [selectedTaskID, setSelectedTaskID] = useState('')
  const [events, setEvents] = useState<StreamEvent[]>([])
  const [lastEventID, setLastEventID] = useState(0)
  const [deadLetters, setDeadLetters] = useState<DeadLetterTask[]>([])
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [streamState, setStreamState] = useState<StreamState>('idle')
  const [requestError, setRequestError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [refreshingDeadLetters, setRefreshingDeadLetters] = useState(false)
  const [refreshingTasks, setRefreshingTasks] = useState(false)
  const [replayingTaskID, setReplayingTaskID] = useState('')
  const [cancelingTaskID, setCancelingTaskID] = useState('')
  const [cancelReason, setCancelReason] = useState(
    language === 'zh' ? '手动停止' : 'manual stop',
  )
  const [selectedTaskIDs, setSelectedTaskIDs] = useState<string[]>([])
  const [bulkCanceling, setBulkCanceling] = useState(false)
  const [taskStatusFilter, setTaskStatusFilter] = useState<'all' | TaskStatus>('all')
  const [batchCancelHistory, setBatchCancelHistory] = useState<BatchCancelResult[]>([])
  const [expandedBatchResultIDs, setExpandedBatchResultIDs] = useState<string[]>([])
  const [copyFeedback, setCopyFeedback] = useState<{
    resultID: string
    state: 'copied' | 'failed'
  } | null>(null)

  // EventSource 与最近事件 ID 放在渲染流程外维护，用于任务切换时续传 SSE。
  const eventSourceRef = useRef<EventSource | null>(null)
  const lastEventRef = useRef(0)

  const selectedTask = useMemo(
    () => tasks.find((task) => task.id === selectedTaskID),
    [tasks, selectedTaskID],
  )

  const isCancelableTask = (task: Task): boolean =>
    task.status === 'queued' || task.status === 'running'

  const selectedCancelableTaskIDs = useMemo(
    () =>
      selectedTaskIDs.filter((taskID) => {
        const task = tasks.find((item) => item.id === taskID)
        return !!task && isCancelableTask(task)
      }),
    [selectedTaskIDs, tasks],
  )

  const normalizedUserID = userID.trim()
  const myTasks = useMemo(
    () => tasks.filter((task) => normalizedUserID !== '' && task.user_id === normalizedUserID),
    [tasks, normalizedUserID],
  )

  const taskStatusLabel = (status?: string): string => {
    switch (status) {
      case 'queued':
        return tr('排队中', 'queued')
      case 'running':
        return tr('执行中', 'running')
      case 'completed':
        return tr('已完成', 'completed')
      case 'failed':
        return tr('失败', 'failed')
      case 'canceled':
        return tr('已取消', 'canceled')
      default:
        return status ?? tr('未知', 'unknown')
    }
  }

  const streamStateLabel = (state: StreamState): string => {
    switch (state) {
      case 'idle':
        return tr('空闲', 'idle')
      case 'connecting':
        return tr('连接中', 'connecting')
      case 'live':
        return tr('实时', 'live')
      case 'closed':
        return tr('已关闭', 'closed')
      default:
        return state
    }
  }

  const eventTypeLabel = (eventType?: string): string => {
    switch (eventType) {
      case 'info':
        return tr('信息', 'info')
      case 'started':
        return tr('开始', 'started')
      case 'token':
        return tr('Token', 'token')
      case 'cancel_requested':
        return tr('收到取消请求', 'cancel requested')
      case 'canceled':
        return tr('已取消', 'canceled')
      case 'completed':
        return tr('已完成', 'completed')
      case 'failed':
        return tr('失败', 'failed')
      case 'dead_lettered':
        return tr('进入死信', 'dead-lettered')
      case 'replay_requested':
        return tr('已请求重放', 'replay requested')
      case 'terminal':
        return tr('终态', 'terminal')
      case 'unspecified':
        return tr('未指定', 'unspecified')
      default:
        return eventType ?? tr('未知', 'unknown')
    }
  }

  const healthStatusLabel = (status?: string): string => {
    switch (status) {
      case 'ok':
        return tr('正常', 'ok')
      case 'degraded':
        return tr('降级', 'degraded')
      default:
        return status ?? tr('未知', 'unknown')
    }
  }

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }

    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language)
  }, [language])

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }

    window.localStorage.setItem(VIEW_MODE_STORAGE_KEY, viewMode)
  }, [viewMode])

  // 让 ref 与状态同步，保证新建 EventSource 时可从最新事件断点续传。
  useEffect(() => {
    lastEventRef.current = lastEventID
  }, [lastEventID])

  // 清理已不在任务列表中的选中项。
  useEffect(() => {
    setSelectedTaskIDs((previous) => {
      const next = previous.filter((taskID) => tasks.some((task) => task.id === taskID))
      if (next.length === previous.length && next.every((taskID, index) => taskID === previous[index])) {
        return previous
      }
      return next
    })
  }, [tasks])

  const upsertTask = (incoming: Task) => {
    setTasks((previous) => {
      const next = [...previous]
      const index = next.findIndex((item) => item.id === incoming.id)
      if (index >= 0) {
        next[index] = incoming
      } else {
        next.unshift(incoming)
      }

      // 维持“最近更新优先”的稳定排序，便于控制台阅读。
      next.sort(
        (left, right) =>
          new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime(),
      )
      return next
    })
  }

  const refreshTasks = async () => {
    setRefreshingTasks(true)
    try {
      const params = new URLSearchParams()
      params.set('limit', String(TASK_LIMIT))
      if (taskStatusFilter !== 'all') {
        params.set('status', taskStatusFilter)
      }

      const response = await requestJson<TaskListResponse>(`/v1/tasks?${params.toString()}`)
      setTasks(response.items)
      setSelectedTaskID((previous) => {
        // 尽量保留当前选中任务，否则默认选中最新任务。
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
  }

  const fetchTask = async (taskID: string) => {
    try {
      const task = await requestJson<Task>(`/v1/tasks/${taskID}`)
      upsertTask(task)
    } catch (error) {
      setRequestError(
        error instanceof Error
          ? error.message
          : tr('获取任务状态失败', 'Failed to fetch task state'),
      )
    }
  }

  const refreshDeadLetters = async () => {
    setRefreshingDeadLetters(true)
    try {
      const response = await requestJson<DeadLetterResponse>(
        `/v1/dead-letters?limit=${DEAD_LETTER_LIMIT}`,
      )
      setDeadLetters(response.items)
    } catch (error) {
      setRequestError(
        error instanceof Error
          ? error.message
          : tr('获取死信列表失败', 'Failed to fetch dead letters'),
      )
    } finally {
      setRefreshingDeadLetters(false)
    }
  }

  const refreshHealth = async () => {
    try {
      const nextHealth = await requestJson<HealthResponse>('/healthz')
      setHealth(nextHealth)
    } catch (error) {
      // 健康检查失败不应阻塞其他交互。
      setHealth({
        status: 'degraded',
        error: error instanceof Error ? error.message : tr('网关不可达', 'Gateway unreachable'),
      })
    }
  }

  useEffect(() => {
    // 初始化看板数据，并建立定时刷新循环。
    void refreshHealth()
    void refreshDeadLetters()
    void refreshTasks()

    const healthTimer = window.setInterval(() => {
      void refreshHealth()
    }, 10000)

    const deadLetterTimer = window.setInterval(() => {
      void refreshDeadLetters()
    }, 5000)

    const tasksTimer = window.setInterval(() => {
      void refreshTasks()
    }, 4000)

    return () => {
      window.clearInterval(healthTimer)
      window.clearInterval(deadLetterTimer)
      window.clearInterval(tasksTimer)
    }
  }, [taskStatusFilter])

  // 轮询选中任务状态；SSE 断开重连期间由轮询兜底同步。
  useEffect(() => {
    if (!selectedTaskID) {
      return
    }

    const timer = window.setInterval(() => {
      void fetchTask(selectedTaskID)
    }, 1500)

    return () => {
      window.clearInterval(timer)
    }
  }, [selectedTaskID])

  // 为当前选中任务建立并维护单独 EventSource 连接。
  useEffect(() => {
    if (!selectedTaskID) {
      return
    }

    eventSourceRef.current?.close()
    setStreamState('connecting')

    const streamURL = `/v1/tasks/${selectedTaskID}/events?last_event_id=${lastEventRef.current}`
    const source = new EventSource(streamURL)
    eventSourceRef.current = source

    const onEvent = (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data) as StreamEvent
        const eventType = payload.type ?? event.type

        setStreamState('live')
        setEvents((previous) => {
          // 防止重连后重复投递导致事件重复显示。
          if (
            typeof payload.event_id === 'number' &&
            previous.some((entry) => entry.event_id === payload.event_id)
          ) {
            return previous
          }

          return [
            ...previous,
            {
              ...payload,
              type: eventType,
            },
          ]
        })

        if (typeof payload.event_id === 'number') {
          setLastEventID(payload.event_id)
        }

        if (eventType === 'terminal') {
          // terminal 表示服务端确认该生命周期不再产出新事件。
          setStreamState('closed')
          source.close()
          void fetchTask(selectedTaskID)
          void refreshDeadLetters()
        }
      } catch {
        setRequestError(tr('解析事件流数据失败', 'Failed to parse stream event payload'))
      }
    }

    // 为所有已知事件类型注册统一处理函数。
    STREAM_EVENT_TYPES.forEach((eventType) => {
      source.addEventListener(eventType, onEvent as EventListener)
    })

    source.onerror = () => {
      // 连接异常后转为 closed，等待用户切换任务或刷新后重连。
      setStreamState('closed')
      source.close()
    }

    return () => {
      source.close()
    }
  }, [selectedTaskID])

  const handleCreateTask = async (formEvent: FormEvent<HTMLFormElement>) => {
    formEvent.preventDefault()

    if (!userID.trim() || !prompt.trim()) {
      setRequestError(tr('user_id 和 prompt 不能为空', 'user_id and prompt are required'))
      return
    }

    setSubmitting(true)
    setRequestError('')

    try {
      const created = await requestJson<Task>('/v1/tasks', {
        method: 'POST',
        body: JSON.stringify({
          user_id: userID.trim(),
          prompt: prompt.trim(),
          metadata: {
            source: 'web-console',
          },
        }),
      })

      upsertTask(created)
      setSelectedTaskID(created.id)
      setEvents([])
      setLastEventID(0)
      // 创建后刷新列表，确保与服务端排序和状态一致。
      await refreshTasks()
    } catch (error) {
      setRequestError(
        error instanceof Error
          ? error.message
          : tr('创建任务失败', 'Failed to create task'),
      )
    } finally {
      setSubmitting(false)
    }
  }

  const handleSelectTask = (taskID: string) => {
    setRequestError('')
    setSelectedTaskID(taskID)
    // 切换任务时重置本地事件视图和游标。
    setEvents([])
    setLastEventID(0)
  }

  const handleReplay = async (taskID: string) => {
    setReplayingTaskID(taskID)
    setRequestError('')

    try {
      const replayed = await requestJson<Task>(`/v1/tasks/${taskID}/replay`, {
        method: 'POST',
      })

      upsertTask(replayed)
      setSelectedTaskID(taskID)
      setEvents([])
      setLastEventID(0)
      await refreshTasks()
      await refreshDeadLetters()
    } catch (error) {
      setRequestError(
        error instanceof Error
          ? error.message
          : tr('重放任务失败', 'Failed to replay task'),
      )
    } finally {
      setReplayingTaskID('')
    }
  }

  const handleCancelTask = async (taskID: string) => {
    setCancelingTaskID(taskID)
    setRequestError('')

    try {
      const canceled = await requestJson<Task>(`/v1/tasks/${taskID}/cancel`, {
        method: 'POST',
        body: JSON.stringify({
          requested_by: userID.trim() || 'web-console',
          reason: cancelReason.trim(),
        }),
      })

      upsertTask(canceled)
      setSelectedTaskIDs((previous) => previous.filter((id) => id !== taskID))
      if (selectedTaskID === taskID) {
        // 立即刷新当前任务，快速同步终态。
        await fetchTask(taskID)
      }
      await refreshTasks()
      await refreshDeadLetters()
    } catch (error) {
      setRequestError(
        error instanceof Error
          ? error.message
          : tr('取消任务失败', 'Failed to cancel task'),
      )
    } finally {
      setCancelingTaskID('')
    }
  }

  const toggleTaskSelection = (taskID: string, checked: boolean) => {
    setSelectedTaskIDs((previous) => {
      if (checked) {
        if (previous.includes(taskID)) {
          return previous
        }
        return [...previous, taskID]
      }

      return previous.filter((id) => id !== taskID)
    })
  }

  const toggleSelectAllCancelable = (checked: boolean) => {
    const cancelableIDs = tasks.filter(isCancelableTask).map((task) => task.id)

    setSelectedTaskIDs((previous) => {
      if (checked) {
        return Array.from(new Set([...previous, ...cancelableIDs]))
      }
      return previous.filter((id) => !cancelableIDs.includes(id))
    })
  }

  const handleBatchCancel = async () => {
    if (selectedCancelableTaskIDs.length === 0) {
      return
    }

    setBulkCanceling(true)
    setRequestError('')
    setCopyFeedback(null)

    try {
      const response = await requestJson<BatchCancelResponse>('/v1/tasks/cancel', {
        method: 'POST',
        body: JSON.stringify({
          task_ids: selectedCancelableTaskIDs,
          requested_by: userID.trim() || 'web-console',
          reason: cancelReason.trim(),
        }),
      })

      const nextResult: BatchCancelResult = {
        id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
        generated_at_unix_ms: Date.now(),
        response,
      }

      // 保留短期历史窗口，便于排查连续批量操作。
      setBatchCancelHistory((previous) => [nextResult, ...previous].slice(0, BATCH_RESULT_HISTORY_LIMIT))
      setExpandedBatchResultIDs((previous) => [
        nextResult.id,
        ...previous.filter((resultID) => resultID !== nextResult.id).slice(0, BATCH_RESULT_HISTORY_LIMIT - 1),
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

      response.canceled.forEach((task) => {
        upsertTask(task)
      })

      if (response.failed_count > 0) {
        const detail = response.failed
          .slice(0, 3)
          .map((item) => `${item.task_id}: ${item.error}`)
          .join('; ')
        setRequestError(
          language === 'zh'
            ? `批量取消部分失败（${response.failed_count} 项）：${detail}`
            : `Partial cancel failure (${response.failed_count}): ${detail}`,
        )
      }

      const failedIDs = new Set(response.failed.map((item) => item.task_id))
      // 失败项保持选中，方便操作员快速重试。
      setSelectedTaskIDs((previous) => previous.filter((id) => failedIDs.has(id)))

      await refreshTasks()
      await refreshDeadLetters()
    } catch (error) {
      setRequestError(
        error instanceof Error
          ? error.message
          : tr('批量取消失败', 'Failed to cancel selected tasks'),
      )
    } finally {
      setBulkCanceling(false)
    }
  }

  const toggleBatchResultExpanded = (resultID: string) => {
    setExpandedBatchResultIDs((previous) => {
      if (previous.includes(resultID)) {
        return previous.filter((id) => id !== resultID)
      }
      return [...previous, resultID]
    })
  }

  const handleCopyFailedTaskIDs = async (result: BatchCancelResult) => {
    if (result.response.failed.length === 0) {
      return
    }

    // 仅复制失败任务 ID，便于直接粘贴重试。
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
  }

  if (viewMode === 'client') {
    return (
      <div className="app-shell">
        <header className="topbar">
          <div>
            <p className="eyebrow">{tr('Synapse 用户端', 'Synapse Client')}</p>
            <h1>{tr('智能任务客户端', 'Task Client')}</h1>
          </div>
          <div className="topbar-actions">
            <button className="mode-switch ghost" onClick={() => setViewMode('ops')} type="button">
              {tr('进入运维台', 'Open Ops Console')}
            </button>
            <button
              className="language-switch"
              onClick={() => setLanguage((previous) => (previous === 'zh' ? 'en' : 'zh'))}
              type="button"
            >
              {language === 'zh' ? 'EN' : '中文'}
            </button>

            <div className="health-card">
              <p>{tr('网关健康状态', 'Gateway Health')}</p>
              <strong className={statusClass(health?.status)}>{healthStatusLabel(health?.status)}</strong>
              <span>{health?.model_provider ?? health?.error ?? tr('暂无提供方信息', 'No provider data')}</span>
            </div>
          </div>
        </header>

        {requestError && <p className="error-banner">{requestError}</p>}

        <main className="client-grid">
          <section className="panel panel-client-compose">
            <h2>{tr('提交请求', 'Submit Request')}</h2>
            <form className="compose-form" onSubmit={handleCreateTask}>
              <label>
                {tr('用户 ID', 'User ID')}
                <input
                  value={userID}
                  onChange={(event) => setUserID(event.target.value)}
                  placeholder={tr('例如：alice', 'for example: alice')}
                />
              </label>

              <label>
                {tr('提示词', 'Prompt')}
                <textarea
                  value={prompt}
                  onChange={(event) => setPrompt(event.target.value)}
                  rows={5}
                  placeholder={tr('请输入你要让系统执行的任务', 'Describe what Synapse should execute')}
                />
              </label>

              <button disabled={submitting} type="submit">
                {submitting ? tr('提交中...', 'Submitting...') : tr('发送任务', 'Send Task')}
              </button>
            </form>
          </section>

          <section className="panel panel-client-tasks">
            <div className="panel-head">
              <h2>{tr('我的任务', 'My Tasks')}</h2>
              <button
                className="ghost"
                onClick={() => {
                  void refreshTasks()
                }}
                type="button"
              >
                {refreshingTasks ? tr('刷新中...', 'Refreshing...') : tr('刷新', 'Refresh')}
              </button>
            </div>

            <ul className="task-list">
              {myTasks.map((task) => (
                <li key={task.id}>
                  <button
                    className={task.id === selectedTaskID ? 'task-item active' : 'task-item'}
                    onClick={() => handleSelectTask(task.id)}
                    type="button"
                  >
                    <div>
                      <p>{task.prompt}</p>
                      <small>{task.id.slice(0, 8)}</small>
                    </div>
                    <span className={statusClass(task.status)}>{taskStatusLabel(task.status)}</span>
                  </button>
                </li>
              ))}
              {myTasks.length === 0 && (
                <li className="empty">
                  {normalizedUserID === ''
                    ? tr('先填写用户 ID，再查看你的任务列表。', 'Enter user ID to view your tasks.')
                    : tr('你还没有任务，先提交一个吧。', 'No tasks yet. Submit one to begin.')}
                </li>
              )}
            </ul>
          </section>

          <section className="panel panel-stream panel-client-stream">
            <div className="panel-head">
              <h2>{tr('任务事件流', 'Task Event Stream')}</h2>
              <span className={`stream-${streamState}`}>{streamStateLabel(streamState)}</span>
            </div>

            {selectedTask && selectedTask.user_id === normalizedUserID ? (
              <div className="selected-meta">
                <span className="selected-task-id">{selectedTask.id}</span>
                <div className="selected-actions">
                  <span className={statusClass(selectedTask.status)}>{taskStatusLabel(selectedTask.status)}</span>
                </div>
              </div>
            ) : (
              <p className="empty">{tr('选择一个你的任务以查看实时事件。', 'Select one of your tasks to stream events.')}</p>
            )}

            <ul className="event-list">
              {events.map((event, index) => (
                <li key={`${event.event_id ?? 'meta'}-${index}`} className="event-item">
                  <span className="event-time">{formatDateTime(event.emitted_at_unix_ms)}</span>
                  <span className="event-type">{eventTypeLabel(event.type)}</span>
                  <div className="event-content">
                    {event.token && <code>{event.token}</code>}
                    {!event.token && (event.message || event.status) && (
                      <p>{event.message ?? event.status}</p>
                    )}
                  </div>
                </li>
              ))}
              {events.length === 0 && <li className="empty">{tr('暂无事件。', 'No events yet.')}</li>}
            </ul>
          </section>
        </main>
      </div>
    )
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">{tr('Synapse 指挥中心', 'Synapse Mission Control')}</p>
          <h1>{tr('Agent 运维控制台', 'Agent Operations Console')}</h1>
        </div>
        <div className="topbar-actions">
          <button className="mode-switch ghost" onClick={() => setViewMode('client')} type="button">
            {tr('进入用户端', 'Open Client')}
          </button>
          <button
            className="language-switch"
            onClick={() => setLanguage((previous) => (previous === 'zh' ? 'en' : 'zh'))}
            type="button"
          >
            {language === 'zh' ? 'EN' : '中文'}
          </button>

          <div className="health-card">
            <p>{tr('网关健康状态', 'Gateway Health')}</p>
            <strong className={statusClass(health?.status)}>
              {healthStatusLabel(health?.status)}
            </strong>
            <span>
              {health?.model_provider ?? health?.error ?? tr('暂无提供方信息', 'No provider data')}
            </span>
          </div>
        </div>
      </header>

      {requestError && <p className="error-banner">{requestError}</p>}

      <main className="dashboard-grid">
        <section className="panel panel-compose">
          <h2>{tr('创建任务', 'Launch Task')}</h2>
          <form className="compose-form" onSubmit={handleCreateTask}>
            <label>
              {tr('用户 ID', 'User ID')}
              <input
                value={userID}
                onChange={(event) => setUserID(event.target.value)}
                placeholder={tr('操作者', 'operator')}
              />
            </label>

            <label>
              {tr('提示词', 'Prompt')}
              <textarea
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                rows={5}
                placeholder={tr('描述 Synapse 需要执行的内容', 'Describe what Synapse should execute')}
              />
            </label>

            <button disabled={submitting} type="submit">
              {submitting ? tr('提交中...', 'Submitting...') : tr('加入队列', 'Queue Task')}
            </button>
          </form>
        </section>

        <section className="panel panel-tasks">
          <div className="panel-head">
            <h2>{tr('最近任务', 'Recent Tasks')}</h2>
            <div className="panel-actions">
              <label className="select-all">
                <input
                  type="checkbox"
                  checked={
                    selectedCancelableTaskIDs.length > 0 &&
                    selectedCancelableTaskIDs.length === tasks.filter(isCancelableTask).length
                  }
                  onChange={(event) => toggleSelectAllCancelable(event.target.checked)}
                />
                {tr('全选', 'all')}
              </label>
              <select
                aria-label={tr('按状态过滤任务', 'filter tasks by status')}
                value={taskStatusFilter}
                onChange={(event) => setTaskStatusFilter(event.target.value as 'all' | TaskStatus)}
              >
                <option value="all">{tr('全部', 'all')}</option>
                <option value="queued">{tr('排队中', 'queued')}</option>
                <option value="running">{tr('执行中', 'running')}</option>
                <option value="completed">{tr('已完成', 'completed')}</option>
                <option value="failed">{tr('失败', 'failed')}</option>
                <option value="canceled">{tr('已取消', 'canceled')}</option>
              </select>
              <button
                className="ghost"
                onClick={() => {
                  void refreshTasks()
                }}
                type="button"
              >
                {refreshingTasks ? tr('刷新中...', 'Refreshing...') : tr('刷新', 'Refresh')}
              </button>
              <span>{tasks.length}</span>
            </div>
          </div>
          <div className="batch-toolbar">
            <input
              aria-label={tr('取消原因', 'cancellation reason')}
              value={cancelReason}
              onChange={(event) => setCancelReason(event.target.value)}
              placeholder={tr('取消原因', 'cancel reason')}
            />
            <button
              className="danger"
              disabled={bulkCanceling || selectedCancelableTaskIDs.length === 0}
              onClick={() => {
                void handleBatchCancel()
              }}
              type="button"
            >
              {bulkCanceling
                ? tr('正在取消所选任务...', 'Canceling selected...')
                : language === 'zh'
                  ? `取消所选（${selectedCancelableTaskIDs.length}）`
                  : `Cancel Selected (${selectedCancelableTaskIDs.length})`}
            </button>
          </div>
          {batchCancelHistory.length > 0 && (
            <section className="batch-result-history" aria-live="polite">
              <div className="batch-history-head">
                <h3>{tr('批量取消记录', 'Batch Cancel History')}</h3>
                <span>{batchCancelHistory.length}</span>
              </div>
              <ul className="batch-history-list">
                {batchCancelHistory.map((result) => {
                  const expanded = expandedBatchResultIDs.includes(result.id)
                  const copyState =
                    copyFeedback?.resultID === result.id ? copyFeedback.state : null

                  return (
                    <li key={result.id} className={expanded ? 'batch-result expanded' : 'batch-result'}>
                      <button
                        className="batch-result-toggle"
                        onClick={() => toggleBatchResultExpanded(result.id)}
                        type="button"
                      >
                        <div className="batch-result-head">
                          <h4>{formatDateTime(result.generated_at_unix_ms)}</h4>
                          <span>{expanded ? tr('收起', 'Collapse') : tr('展开', 'Expand')}</span>
                        </div>
                        <div className="batch-metrics">
                          <span>
                            {tr('请求数', 'requested')}: {result.response.requested}
                          </span>
                          <span>
                            {tr('处理数', 'processed')}: {result.response.canceled_count}
                          </span>
                          <span>
                            {tr('已取消', 'already canceled')}: {result.response.already_canceled_count}
                          </span>
                          <span>
                            {tr('失败数', 'failed')}: {result.response.failed_count}
                          </span>
                        </div>
                      </button>

                      {expanded && (
                        <div className="batch-groups">
                          <div className="batch-group batch-group-success">
                            <div className="batch-group-head">
                              <h5>{tr('已处理', 'Processed')}</h5>
                              <span>{result.response.canceled.length}</span>
                            </div>
                            <ul>
                              {result.response.canceled.map((task) => (
                                <li key={`c-${result.id}-${task.id}`}>
                                  <span>{task.id}</span>
                                  <em>{taskStatusLabel(task.status)}</em>
                                </li>
                              ))}
                              {result.response.canceled.length === 0 && (
                                <li className="batch-empty">{tr('暂无已处理任务。', 'No processed tasks.')}</li>
                              )}
                            </ul>
                          </div>

                          <div className="batch-group batch-group-failed">
                            <div className="batch-group-head">
                              <h5>{tr('失败项', 'Failed')}</h5>
                              <div className="batch-failed-actions">
                                <span>{result.response.failed.length}</span>
                                <button
                                  className="ghost small"
                                  disabled={result.response.failed.length === 0}
                                  onClick={() => {
                                    void handleCopyFailedTaskIDs(result)
                                  }}
                                  type="button"
                                >
                                  {copyState === 'copied'
                                    ? tr('已复制', 'Copied')
                                    : copyState === 'failed'
                                      ? tr('复制失败', 'Copy Failed')
                                      : tr('复制失败 ID', 'Copy Failed IDs')}
                                </button>
                              </div>
                            </div>
                            <ul>
                              {result.response.failed.map((item) => (
                                <li key={`f-${result.id}-${item.task_id}`}>
                                  <span>{item.task_id}</span>
                                  <small>{item.error}</small>
                                </li>
                              ))}
                              {result.response.failed.length === 0 && (
                                <li className="batch-empty">{tr('暂无失败任务。', 'No failed tasks.')}</li>
                              )}
                            </ul>
                          </div>
                        </div>
                      )}
                    </li>
                  )
                })}
              </ul>
            </section>
          )}
          <ul className="task-list">
            {tasks.map((task) => (
              <li key={task.id} className="task-row">
                <label className="task-check">
                  <input
                    type="checkbox"
                    checked={selectedTaskIDs.includes(task.id)}
                    onChange={(event) => toggleTaskSelection(task.id, event.target.checked)}
                    disabled={!isCancelableTask(task)}
                  />
                </label>
                <button
                  className={task.id === selectedTaskID ? 'task-item active' : 'task-item'}
                  onClick={() => handleSelectTask(task.id)}
                  type="button"
                >
                  <div>
                    <p>{task.prompt}</p>
                    <small>{task.id.slice(0, 8)}</small>
                  </div>
                  <span className={statusClass(task.status)}>{taskStatusLabel(task.status)}</span>
                </button>
              </li>
            ))}
            {tasks.length === 0 && (
              <li className="empty">
                {tr('当前还没有任务，先创建一个吧。', 'No tasks yet. Create one to begin.')}
              </li>
            )}
          </ul>
        </section>

        <section className="panel panel-stream">
          <div className="panel-head">
            <h2>{tr('实时事件流', 'Live Event Stream')}</h2>
            <span className={`stream-${streamState}`}>{streamStateLabel(streamState)}</span>
          </div>

          {selectedTask ? (
            <div className="selected-meta">
              <span className="selected-task-id">{selectedTask.id}</span>
              <div className="selected-actions">
                <span className={statusClass(selectedTask.status)}>{taskStatusLabel(selectedTask.status)}</span>
                {(selectedTask.status === 'queued' || selectedTask.status === 'running') && (
                  <button
                    className="danger"
                    disabled={cancelingTaskID === selectedTask.id}
                    onClick={() => {
                      void handleCancelTask(selectedTask.id)
                    }}
                    type="button"
                  >
                    {cancelingTaskID === selectedTask.id
                      ? tr('取消中...', 'Canceling...')
                      : tr('取消', 'Cancel')}
                  </button>
                )}
              </div>
            </div>
          ) : (
            <p className="empty">{tr('选择一个任务以查看事件流。', 'Select a task to stream events.')}</p>
          )}

          <ul className="event-list">
            {events.map((event, index) => (
              <li key={`${event.event_id ?? 'meta'}-${index}`} className="event-item">
                <span className="event-time">{formatDateTime(event.emitted_at_unix_ms)}</span>
                <span className="event-type">{eventTypeLabel(event.type)}</span>
                <div className="event-content">
                  {event.token && <code>{event.token}</code>}
                  {!event.token && (event.message || event.status) && (
                    <p>{event.message ?? event.status}</p>
                  )}
                </div>
              </li>
            ))}
            {events.length === 0 && <li className="empty">{tr('暂无事件。', 'No events yet.')}</li>}
          </ul>
        </section>

        <section className="panel panel-dead">
          <div className="panel-head">
            <h2>{tr('死信任务', 'Dead Letters')}</h2>
            <button
              className="ghost"
              onClick={() => {
                void refreshDeadLetters()
              }}
              type="button"
            >
              {refreshingDeadLetters ? tr('刷新中...', 'Refreshing...') : tr('刷新', 'Refresh')}
            </button>
          </div>

          <ul className="dead-list">
            {deadLetters.map((entry) => (
              <li key={entry.task_id} className="dead-item">
                <div>
                  <p>{entry.task_id}</p>
                  <small>{entry.reason}</small>
                </div>
                <div className="dead-actions">
                  <span>
                    {tr('尝试次数', 'attempts')}: {entry.attempts}
                  </span>
                  <button
                    disabled={replayingTaskID === entry.task_id}
                    onClick={() => {
                      void handleReplay(entry.task_id)
                    }}
                    type="button"
                  >
                    {replayingTaskID === entry.task_id
                      ? tr('重放中...', 'Replaying...')
                      : tr('重放', 'Replay')}
                  </button>
                </div>
              </li>
            ))}
            {deadLetters.length === 0 && <li className="empty">{tr('暂无死信任务。', 'No dead letters.')}</li>}
          </ul>
          <div className="panel-footnote">
            <small>
              {tr('更新时间', 'Updated')}: {formatDateTime(Date.now())}
            </small>
          </div>
        </section>
      </main>
    </div>
  )
}

export default App
