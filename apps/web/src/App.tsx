import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkBreaks from 'remark-breaks'
import remarkGfm from 'remark-gfm'
import './App.css'

// 控制台展示与筛选使用的任务生命周期状态。
type TaskStatus = 'queued' | 'running' | 'completed' | 'failed' | 'canceled'
type Language = 'zh' | 'en'
type ViewMode = 'client' | 'ops'
type UserRole = 'admin' | 'user'
type AuthMode = 'login' | 'register'

type Task = {
  id: string
  user_id: string
  prompt: string
  status: TaskStatus
  error?: string
  metadata?: Record<string, string>
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

type SessionIdentity = {
  username: string
  role: UserRole
}

type AuthPayload = {
  user: SessionIdentity
  expires_at?: string
}

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
const AUTH_SESSION_STORAGE_KEY = 'synapse.web.auth.session'
const CLIENT_CONVERSATION_ID_KEY = 'conversation_id'
const CLIENT_USER_MESSAGE_KEY = 'user_message'
const NEW_CONVERSATION_DRAFT_ID = '__draft__'

function normalizeUsername(value: string): string {
  return value.trim().toLowerCase()
}

function loadSessionFromStorage(): SessionIdentity | null {
  if (typeof window === 'undefined') {
    return null
  }

  try {
    const raw = window.localStorage.getItem(AUTH_SESSION_STORAGE_KEY)
    if (!raw) {
      return null
    }

    const parsed = JSON.parse(raw) as Partial<SessionIdentity>
    if (typeof parsed.username !== 'string') {
      return null
    }

    if (parsed.role !== 'admin' && parsed.role !== 'user') {
      return null
    }

    const normalized = normalizeUsername(parsed.username)
    if (normalized === '') {
      return null
    }

    return {
      username: normalized,
      role: parsed.role,
    }
  } catch {
    return null
  }
}

function createConversationID(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }

  return `conv-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function truncatePreview(text: string, limit: number): string {
  const normalized = text.trim()
  if (normalized.length <= limit) {
    return normalized
  }

  return `${normalized.slice(0, limit)}...`
}

function ChatMarkdown({ content }: { content: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkBreaks]}
      components={{
        a: ({ href, children }) => {
          const normalizedHref = typeof href === 'string' ? href.trim() : ''
          if (normalizedHref === '') {
            return <span>{children}</span>
          }

          return (
            <a href={normalizedHref} rel="noreferrer noopener" target="_blank">
              {children}
            </a>
          )
        },
      }}
    >
      {content}
    </ReactMarkdown>
  )
}

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
    credentials: init?.credentials ?? 'include',
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

  const [currentUser, setCurrentUser] = useState<SessionIdentity | null>(() =>
    loadSessionFromStorage(),
  )
  const [authInitializing, setAuthInitializing] = useState(true)
  const [authMode, setAuthMode] = useState<AuthMode>('login')
  const [authUsername, setAuthUsername] = useState('')
  const [authPassword, setAuthPassword] = useState('')
  const [authConfirmPassword, setAuthConfirmPassword] = useState('')
  const [authError, setAuthError] = useState('')
  const [authNotice, setAuthNotice] = useState('')

  // 表单输入与选择态。
  const [userID, setUserID] = useState('founder')
  const [prompt, setPrompt] = useState('')
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
  const [selectedConversationID, setSelectedConversationID] = useState('')
  const [responseByTaskID, setResponseByTaskID] = useState<Record<string, string>>({})

  // EventSource 与最近事件 ID 放在渲染流程外维护，用于任务切换时续传 SSE。
  const eventSourceRef = useRef<EventSource | null>(null)
  const taskLastEventIDRef = useRef<Record<string, number>>({})
  const hydratingTaskIDsRef = useRef<Set<string>>(new Set())
  const hydrationSourcesRef = useRef<Map<string, EventSource>>(new Map())
  const clientTranscriptRef = useRef<HTMLDivElement | null>(null)
  const transcriptPinnedToBottomRef = useRef(true)
  const lastTranscriptConversationIDRef = useRef('')

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

  const currentScopedUserID = currentUser?.username ?? ''
  const isAdmin = currentUser?.role === 'admin'

  const myTasks = useMemo(
    () => tasks.filter((task) => currentScopedUserID !== '' && task.user_id === currentScopedUserID),
    [tasks, currentScopedUserID],
  )

  const knownUsers = useMemo(() => {
    const latestByUser = new Map<string, number>()

    tasks.forEach((task) => {
      const user = task.user_id.trim()
      if (!user) {
        return
      }

      const updatedAt = new Date(task.updated_at).getTime()
      const previous = latestByUser.get(user)
      if (previous === undefined || updatedAt > previous) {
        latestByUser.set(user, updatedAt)
      }
    })

    return Array.from(latestByUser.entries())
      .sort((left, right) => right[1] - left[1])
      .map(([user]) => user)
  }, [tasks])

  const conversationTasksByID = useMemo(() => {
    const grouped = new Map<string, Task[]>()

    myTasks.forEach((task) => {
      const conversationID = task.metadata?.[CLIENT_CONVERSATION_ID_KEY]?.trim() || task.id
      const existing = grouped.get(conversationID) ?? []
      existing.push(task)
      grouped.set(conversationID, existing)
    })

    grouped.forEach((items) => {
      items.sort(
        (left, right) =>
          new Date(left.created_at).getTime() - new Date(right.created_at).getTime(),
      )
    })

    return grouped
  }, [myTasks])

  const clientConversations = useMemo(
    () =>
      Array.from(conversationTasksByID.entries())
        .map(([id, items]) => {
          const firstTask = items[0]
          const latestTask = items[items.length - 1]
          const firstMessage = firstTask.metadata?.[CLIENT_USER_MESSAGE_KEY] || firstTask.prompt
          const previewMessage = latestTask.metadata?.[CLIENT_USER_MESSAGE_KEY] || latestTask.prompt

          return {
            id,
            title: truncatePreview(firstMessage, 28),
            preview: truncatePreview(previewMessage, 52),
            latestTask,
            taskCount: items.length,
          }
        })
        .sort(
          (left, right) =>
            new Date(right.latestTask.updated_at).getTime() -
            new Date(left.latestTask.updated_at).getTime(),
        ),
    [conversationTasksByID],
  )

  const selectedConversationTasks = useMemo(() => {
    if (!selectedConversationID || selectedConversationID === NEW_CONVERSATION_DRAFT_ID) {
      return []
    }

    return conversationTasksByID.get(selectedConversationID) ?? []
  }, [conversationTasksByID, selectedConversationID])

  const selectedConversation =
    selectedConversationID && selectedConversationID !== NEW_CONVERSATION_DRAFT_ID
      ? clientConversations.find((conversation) => conversation.id === selectedConversationID) || null
      : null

  const activeConversationTask =
    selectedConversationTasks.length > 0
      ? selectedConversationTasks[selectedConversationTasks.length - 1]
      : null

  const isActiveAssistantStreaming =
    !!activeConversationTask &&
    (activeConversationTask.status === 'queued' ||
      activeConversationTask.status === 'running' ||
      streamState === 'connecting' ||
      streamState === 'live')

  const activeConversationTitle =
    selectedConversationID === NEW_CONVERSATION_DRAFT_ID
      ? tr('新对话', 'New Chat')
      : selectedConversation?.title || tr('会话', 'Conversation')

  const summarizeTaskError = (errorText?: string): string => {
    const normalized = (errorText ?? '').trim()
    if (normalized === '') {
      return tr('任务执行失败。', 'Task failed during execution.')
    }

    const lowered = normalized.toLowerCase()
    if (
      lowered.includes('resource_exhausted') ||
      lowered.includes('quota') ||
      lowered.includes('rate limit') ||
      lowered.includes('429')
    ) {
      return tr(
        '模型服务触发配额或限流，请稍后重试。',
        'Model provider quota or rate limit reached. Please retry later.',
      )
    }

    if (
      lowered.includes('invalid api key') ||
      lowered.includes('unauthorized') ||
      lowered.includes('permission denied')
    ) {
      return tr(
        '模型服务鉴权失败，请检查 API Key 与服务配置。',
        'Model provider authentication failed. Please verify API key and provider config.',
      )
    }

    if (normalized.length > 260) {
      return `${normalized.slice(0, 260)}...`
    }

    return normalized
  }

  const assistantTextForTask = (task: Task): string => {
    const cached = responseByTaskID[task.id]
    if (cached && cached.trim() !== '') {
      return cached
    }

    switch (task.status) {
      case 'queued':
        return tr('正在排队处理中...', 'Queued and waiting for execution...')
      case 'running':
        return tr('正在生成回复...', 'Generating response...')
      case 'completed':
        return tr('该任务已完成，但本地尚未缓存回复内容。', 'Task completed but response text is not cached yet.')
      case 'failed':
        return summarizeTaskError(task.error)
      case 'canceled':
        return task.error || tr('任务已取消。', 'Task was canceled.')
      default:
        return tr('等待回复中。', 'Waiting for response.')
    }
  }

  const handleTranscriptScroll = () => {
    const container = clientTranscriptRef.current
    if (!container) {
      return
    }

    const distanceToBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight
    transcriptPinnedToBottomRef.current = distanceToBottom <= 44
  }

  const conversationMessages = useMemo(
    () =>
      selectedConversationTasks.flatMap((task) => {
        const userMessage = task.metadata?.[CLIENT_USER_MESSAGE_KEY] || task.prompt
        return [
          {
            id: `${task.id}-user`,
            role: 'user' as const,
            taskID: task.id,
            content: userMessage,
            timestamp: task.created_at,
            status: task.status,
          },
          {
            id: `${task.id}-assistant`,
            role: 'assistant' as const,
            taskID: task.id,
            content: assistantTextForTask(task),
            timestamp: task.updated_at,
            status: task.status,
          },
        ]
      }),
    [language, selectedConversationTasks, responseByTaskID],
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

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }

    if (!currentUser) {
      window.localStorage.removeItem(AUTH_SESSION_STORAGE_KEY)
      return
    }

    window.localStorage.setItem(AUTH_SESSION_STORAGE_KEY, JSON.stringify(currentUser))
  }, [currentUser])

  useEffect(() => {
    let canceled = false

    const bootstrapSession = async () => {
      try {
        const payload = await requestJson<AuthPayload>('/v1/auth/me')
        if (canceled) {
          return
        }

        setCurrentUser(payload.user)
        setUserID(payload.user.username)
      } catch {
        if (canceled) {
          return
        }

        setCurrentUser(null)
      } finally {
        if (!canceled) {
          setAuthInitializing(false)
        }
      }
    }

    void bootstrapSession()

    return () => {
      canceled = true
    }
  }, [])

  useEffect(() => {
    if (!currentUser) {
      return
    }

    if (userID !== currentUser.username) {
      setUserID(currentUser.username)
    }

    if (!isAdmin && viewMode === 'ops') {
      setViewMode('client')
    }
  }, [currentUser, isAdmin, userID, viewMode])

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

  useEffect(() => {
    if (viewMode !== 'client') {
      return
    }

    if (selectedConversationID === NEW_CONVERSATION_DRAFT_ID) {
      return
    }

    if (clientConversations.length === 0) {
      setSelectedConversationID('')
      return
    }

    if (!selectedConversationID) {
      setSelectedConversationID(clientConversations[0].id)
      return
    }

    if (!clientConversations.some((conversation) => conversation.id === selectedConversationID)) {
      setSelectedConversationID(clientConversations[0].id)
    }
  }, [clientConversations, selectedConversationID, viewMode])

  useEffect(() => {
    if (viewMode !== 'client') {
      return
    }

    if (!activeConversationTask) {
      if (selectedTaskID) {
        setSelectedTaskID('')
        setEvents([])
        setLastEventID(0)
      }
      return
    }

    if (selectedTaskID === activeConversationTask.id) {
      return
    }

    setSelectedTaskID(activeConversationTask.id)
    setEvents([])
    setLastEventID(taskLastEventIDRef.current[activeConversationTask.id] ?? 0)
  }, [activeConversationTask, selectedTaskID, viewMode])

  useEffect(() => {
    if (viewMode !== 'client') {
      hydrationSourcesRef.current.forEach((source) => {
        source.close()
      })
      hydrationSourcesRef.current.clear()
      hydratingTaskIDsRef.current.clear()
      return
    }

    const completedTasks = selectedConversationTasks.filter(
      (task) => task.status === 'completed' || task.status === 'failed' || task.status === 'canceled',
    )

    completedTasks.forEach((task) => {
      const cachedText = responseByTaskID[task.id]
      const alreadyCached = typeof cachedText === 'string' && cachedText.trim() !== ''
      const alreadyHydrating = hydratingTaskIDsRef.current.has(task.id)
      if (alreadyCached || alreadyHydrating) {
        return
      }

      hydratingTaskIDsRef.current.add(task.id)

      let responseText = ''
      let hydratedLastEventID = 0
      let closed = false
      const source = new EventSource(`/v1/tasks/${task.id}/events?last_event_id=0`)
      hydrationSourcesRef.current.set(task.id, source)

      const closeSource = () => {
        if (closed) {
          return
        }

        closed = true
        window.clearTimeout(timeoutID)
        source.close()
        hydrationSourcesRef.current.delete(task.id)
        hydratingTaskIDsRef.current.delete(task.id)
        if (hydratedLastEventID > 0) {
          const previousCursor = taskLastEventIDRef.current[task.id] ?? 0
          taskLastEventIDRef.current[task.id] = Math.max(previousCursor, hydratedLastEventID)
        }
        if (responseText.trim() !== '') {
          setResponseByTaskID((previous) => ({
            ...previous,
            [task.id]: responseText,
          }))
        }
      }

      const timeoutID = window.setTimeout(() => {
        closeSource()
      }, 20000)

      const onEvent = (event: MessageEvent<string>) => {
        try {
          const payload = JSON.parse(event.data) as StreamEvent
          const eventType = payload.type ?? event.type

          if (typeof payload.event_id === 'number') {
            hydratedLastEventID = Math.max(hydratedLastEventID, payload.event_id)
          }

          if (eventType === 'token' && payload.token) {
            responseText += payload.token
          }

          if (eventType === 'terminal') {
            closeSource()
          }
        } catch {
          closeSource()
        }
      }

      STREAM_EVENT_TYPES.forEach((eventType) => {
        source.addEventListener(eventType, onEvent as EventListener)
      })

      source.onerror = () => {
        closeSource()
      }
    })
  }, [responseByTaskID, selectedConversationTasks, viewMode])

  useEffect(() => {
    return () => {
      hydrationSourcesRef.current.forEach((source) => {
        source.close()
      })
      hydrationSourcesRef.current.clear()
      hydratingTaskIDsRef.current.clear()
    }
  }, [])

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

      if (viewMode === 'ops') {
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
      }
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
    if (!isAdmin || viewMode !== 'ops') {
      setDeadLetters([])
      setRequestError((previous) => (previous === 'forbidden' ? '' : previous))
      return
    }

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
    if (!currentUser) {
      return
    }

    // 初始化看板数据，并建立定时刷新循环。
    void refreshHealth()
    void refreshTasks()

    const healthTimer = window.setInterval(() => {
      void refreshHealth()
    }, 10000)

    let deadLetterTimer: number | null = null
    if (isAdmin && viewMode === 'ops') {
      void refreshDeadLetters()
      deadLetterTimer = window.setInterval(() => {
        void refreshDeadLetters()
      }, 5000)
    }

    const tasksTimer = window.setInterval(() => {
      void refreshTasks()
    }, 4000)

    return () => {
      window.clearInterval(healthTimer)
      if (deadLetterTimer !== null) {
        window.clearInterval(deadLetterTimer)
      }
      window.clearInterval(tasksTimer)
    }
  }, [currentUser, isAdmin, taskStatusFilter, viewMode])

  // 轮询选中任务状态；SSE 断开重连期间由轮询兜底同步。
  useEffect(() => {
    if (!currentUser || !selectedTaskID) {
      return
    }

    const timer = window.setInterval(() => {
      void fetchTask(selectedTaskID)
    }, 1500)

    return () => {
      window.clearInterval(timer)
    }
  }, [currentUser, selectedTaskID])

  useEffect(() => {
    if (viewMode !== 'client') {
      return
    }

    const container = clientTranscriptRef.current
    if (!container) {
      return
    }

    const currentConversationID = selectedConversationID || NEW_CONVERSATION_DRAFT_ID
    const conversationChanged =
      lastTranscriptConversationIDRef.current !== currentConversationID

    if (conversationChanged) {
      lastTranscriptConversationIDRef.current = currentConversationID
      transcriptPinnedToBottomRef.current = true
      container.scrollTop = container.scrollHeight
      return
    }

    if (transcriptPinnedToBottomRef.current) {
      container.scrollTop = container.scrollHeight
    }
  }, [conversationMessages, selectedConversationID, viewMode])

  // 为当前选中任务建立并维护单独 EventSource 连接。
  useEffect(() => {
    if (!selectedTaskID) {
      return
    }

    const taskID = selectedTaskID
    const cachedText = responseByTaskID[taskID]
    const shouldReplayFromStart = typeof cachedText !== 'string' || cachedText.trim() === ''
    const resumeFromEventID = shouldReplayFromStart ? 0 : (taskLastEventIDRef.current[taskID] ?? 0)
    let replayedResponse: string | null = resumeFromEventID === 0 ? '' : null

    eventSourceRef.current?.close()
    setStreamState('connecting')
    setLastEventID(resumeFromEventID)

    const streamURL = `/v1/tasks/${taskID}/events?last_event_id=${resumeFromEventID}`
    const source = new EventSource(streamURL)
    eventSourceRef.current = source
    const seenEventIDs = new Set<number>()

    const onEvent = (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data) as StreamEvent
        const eventType = payload.type ?? event.type

        if (typeof payload.event_id === 'number') {
          if (seenEventIDs.has(payload.event_id)) {
            return
          }
          seenEventIDs.add(payload.event_id)
        }

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

        if (eventType === 'token' && payload.token) {
          if (replayedResponse !== null) {
            const rebuiltResponse = replayedResponse + payload.token
            replayedResponse = rebuiltResponse
            setResponseByTaskID((previous) => ({
              ...previous,
              [taskID]: rebuiltResponse,
            }))
          } else {
            setResponseByTaskID((previous) => ({
              ...previous,
              [taskID]: `${previous[taskID] ?? ''}${payload.token}`,
            }))
          }
        }

        if (typeof payload.event_id === 'number') {
          setLastEventID(payload.event_id)
          taskLastEventIDRef.current[taskID] = payload.event_id
        }

        if (eventType === 'terminal') {
          // terminal 表示服务端确认该生命周期不再产出新事件。
          setStreamState('closed')
          source.close()
          void fetchTask(taskID)
          if (isAdmin && viewMode === 'ops') {
            void refreshDeadLetters()
          }
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
  }, [isAdmin, selectedTaskID, viewMode])

  const handleSwitchAuthMode = (nextMode: AuthMode) => {
    setAuthMode(nextMode)
    setAuthError('')
    setAuthNotice('')
    setAuthPassword('')
    setAuthConfirmPassword('')
  }

  const handleLogin = async (formEvent: FormEvent<HTMLFormElement>) => {
    formEvent.preventDefault()

    const normalizedName = normalizeUsername(authUsername)
    if (normalizedName === '' || authPassword === '') {
      setAuthError(tr('用户名和密码不能为空。', 'Username and password are required.'))
      setAuthNotice('')
      return
    }

    try {
      const payload = await requestJson<AuthPayload>('/v1/auth/login', {
        method: 'POST',
        body: JSON.stringify({
          username: normalizedName,
          password: authPassword,
        }),
      })

      setCurrentUser(payload.user)
      setViewMode('client')
      setUserID(payload.user.username)
      setRequestError('')
      setAuthError('')
      setAuthNotice('')
      setAuthPassword('')
      setAuthConfirmPassword('')
    } catch (error) {
      setAuthError(
        error instanceof Error
          ? error.message
          : tr('登录失败，请稍后重试。', 'Sign-in failed, please try again.'),
      )
      setAuthNotice('')
    }
  }

  const handleRegister = async (formEvent: FormEvent<HTMLFormElement>) => {
    formEvent.preventDefault()

    const normalizedName = normalizeUsername(authUsername)
    if (normalizedName === '') {
      setAuthError(tr('用户名不能为空。', 'Username is required.'))
      setAuthNotice('')
      return
    }

    if (normalizedName.length < 3) {
      setAuthError(tr('用户名至少需要 3 个字符。', 'Username must be at least 3 characters.'))
      setAuthNotice('')
      return
    }

    if (authPassword.length < 6) {
      setAuthError(tr('密码至少需要 6 位。', 'Password must be at least 6 characters.'))
      setAuthNotice('')
      return
    }

    if (authPassword !== authConfirmPassword) {
      setAuthError(tr('两次输入的密码不一致。', 'Passwords do not match.'))
      setAuthNotice('')
      return
    }

    try {
      await requestJson<AuthPayload>('/v1/auth/register', {
        method: 'POST',
        body: JSON.stringify({
          username: normalizedName,
          password: authPassword,
        }),
      })

      setAuthMode('login')
      setAuthUsername(normalizedName)
      setAuthPassword('')
      setAuthConfirmPassword('')
      setAuthError('')
      setAuthNotice(
        tr('注册成功，请使用新账号登录。', 'Registration successful. Please sign in with your new account.'),
      )
    } catch (error) {
      setAuthError(
        error instanceof Error
          ? error.message
          : tr('注册失败，请稍后重试。', 'Registration failed, please try again.'),
      )
      setAuthNotice('')
    }
  }

  const handleLogout = async () => {
    try {
      await requestJson<{ status: string }>('/v1/auth/logout', {
        method: 'POST',
      })
    } catch {
      // 无论后端是否可达，都允许前端会话状态回收。
    }

    setCurrentUser(null)
    setViewMode('client')
    setUserID('founder')
    setRequestError('')
    setSelectedConversationID('')
    setSelectedTaskID('')
    setSelectedTaskIDs([])
    setEvents([])
    setLastEventID(0)
    setTasks([])
    setDeadLetters([])
    setResponseByTaskID({})
    taskLastEventIDRef.current = {}
    hydrationSourcesRef.current.forEach((source) => {
      source.close()
    })
    hydrationSourcesRef.current.clear()
    hydratingTaskIDsRef.current.clear()
    setBatchCancelHistory([])
    setExpandedBatchResultIDs([])
    setCopyFeedback(null)
    setStreamState('idle')
    setAuthMode('login')
    setAuthError('')
    setAuthNotice(tr('已退出登录。', 'Signed out successfully.'))
    setAuthUsername('')
    setAuthPassword('')
    setAuthConfirmPassword('')
  }

  const handleCreateTask = async (formEvent: FormEvent<HTMLFormElement>) => {
    formEvent.preventDefault()

    if (!currentUser) {
      setRequestError(tr('请先登录。', 'Please sign in first.'))
      return
    }

    const normalizedUser = currentUser.username
    const messageInput = prompt.trim()

    if (!messageInput) {
      setRequestError(tr('prompt 不能为空', 'prompt is required'))
      return
    }

    if (!normalizedUser) {
      setRequestError(tr('当前账号状态异常，请重新登录。', 'Current account is unavailable, please sign in again.'))
      return
    }

    setSubmitting(true)
    setRequestError('')

    try {
      const inClientMode = viewMode === 'client'
      const nextConversationID = inClientMode
        ? selectedConversationID && selectedConversationID !== NEW_CONVERSATION_DRAFT_ID
          ? selectedConversationID
          : createConversationID()
        : ''

      const metadata: Record<string, string> = {
        source: 'web-console',
      }

      if (inClientMode) {
        metadata.client_view = 'chat'
        metadata[CLIENT_CONVERSATION_ID_KEY] = nextConversationID
        metadata[CLIENT_USER_MESSAGE_KEY] = messageInput
      }

      const created = await requestJson<Task>('/v1/tasks', {
        method: 'POST',
        body: JSON.stringify({
          user_id: normalizedUser,
          prompt: messageInput,
          metadata,
        }),
      })

      upsertTask(created)
      taskLastEventIDRef.current[created.id] = 0
      setSelectedTaskID(created.id)
      setEvents([])
      setLastEventID(0)

      if (inClientMode) {
        transcriptPinnedToBottomRef.current = true
        setSelectedConversationID(nextConversationID)
        setPrompt('')
      }

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
    setLastEventID(taskLastEventIDRef.current[taskID] ?? 0)
  }

  const handleSelectConversation = (conversationID: string) => {
    setRequestError('')
    transcriptPinnedToBottomRef.current = true
    setSelectedConversationID(conversationID)
  }

  const handleStartNewConversation = () => {
    setRequestError('')
    transcriptPinnedToBottomRef.current = true
    setSelectedConversationID(NEW_CONVERSATION_DRAFT_ID)
    setSelectedTaskID('')
    setEvents([])
    setLastEventID(0)
    setPrompt('')
  }

  const handleReplay = async (taskID: string) => {
    setReplayingTaskID(taskID)
    setRequestError('')

    try {
      const replayed = await requestJson<Task>(`/v1/tasks/${taskID}/replay`, {
        method: 'POST',
      })

      upsertTask(replayed)
      taskLastEventIDRef.current[taskID] = 0
      setResponseByTaskID((previous) => ({
        ...previous,
        [taskID]: '',
      }))
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
          requested_by: currentUser?.username || 'web-console',
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
          requested_by: currentUser?.username || 'web-console',
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

  if (authInitializing) {
    return (
      <div className="auth-shell">
        <section className="auth-panel">
          <p className="eyebrow">{tr('正在校验登录状态', 'Checking Session')}</p>
          <h1>{tr('请稍候...', 'Please wait...')}</h1>
          <p className="empty">
            {tr(
              '系统正在与网关同步身份信息。',
              'Synchronizing your authentication state with the gateway.',
            )}
          </p>
        </section>
      </div>
    )
  }

  if (!currentUser) {
    return (
      <div className="auth-shell">
        <section className="auth-hero">
          <p className="eyebrow">{tr('Synapse 安全入口', 'Synapse Secure Access')}</p>
          <h1>{tr('登录后进入控制台', 'Sign In To Continue')}</h1>
          <p>
            {tr(
              '注册普通用户后可使用聊天端，管理员可进入运维台。',
              'Register as a regular user for chat access, and sign in as admin for the ops console.',
            )}
          </p>
        </section>

        <section className="auth-panel">
          <div className="auth-tabs" role="tablist" aria-label={tr('身份操作', 'Authentication actions')}>
            <button
              className={authMode === 'login' ? 'auth-tab active' : 'auth-tab'}
              onClick={() => handleSwitchAuthMode('login')}
              type="button"
            >
              {tr('登录', 'Sign In')}
            </button>
            <button
              className={authMode === 'register' ? 'auth-tab active' : 'auth-tab'}
              onClick={() => handleSwitchAuthMode('register')}
              type="button"
            >
              {tr('注册', 'Register')}
            </button>
          </div>

          {authError && <p className="error-banner">{authError}</p>}
          {authNotice && <p className="auth-notice">{authNotice}</p>}

          <form className="auth-form" onSubmit={authMode === 'login' ? handleLogin : handleRegister}>
            <label>
              {tr('用户名', 'Username')}
              <input
                autoComplete="username"
                onChange={(event) => setAuthUsername(event.target.value)}
                placeholder={tr('输入用户名', 'Enter username')}
                value={authUsername}
              />
            </label>

            <label>
              {tr('密码', 'Password')}
              <input
                autoComplete={authMode === 'login' ? 'current-password' : 'new-password'}
                onChange={(event) => setAuthPassword(event.target.value)}
                placeholder={tr('至少 6 位', 'At least 6 characters')}
                type="password"
                value={authPassword}
              />
            </label>

            {authMode === 'register' && (
              <label>
                {tr('确认密码', 'Confirm Password')}
                <input
                  autoComplete="new-password"
                  onChange={(event) => setAuthConfirmPassword(event.target.value)}
                  placeholder={tr('再次输入密码', 'Repeat your password')}
                  type="password"
                  value={authConfirmPassword}
                />
              </label>
            )}

            <button type="submit">
              {authMode === 'login' ? tr('进入系统', 'Enter Console') : tr('创建账号', 'Create Account')}
            </button>
          </form>

          <p className="auth-footnote">
            {tr(
              '运维台仅管理员可访问，管理员账号由系统预置维护。',
              'Ops console is admin-only, and the administrator account is managed by the system.',
            )}
          </p>
        </section>
      </div>
    )
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
            <div className="account-pill">
              <div>
                <strong>{currentUser.username}</strong>
                <span>{isAdmin ? tr('管理员', 'admin') : tr('普通用户', 'user')}</span>
              </div>
              <button className="ghost small" onClick={handleLogout} type="button">
                {tr('退出', 'Sign Out')}
              </button>
            </div>
            {isAdmin ? (
              <button className="mode-switch ghost" onClick={() => setViewMode('ops')} type="button">
                {tr('进入运维台', 'Open Ops Console')}
              </button>
            ) : (
              <button
                className="mode-switch ghost ops-locked"
                disabled
                title={tr('仅管理员可以进入运维台。', 'Ops console is available for admin only.')}
                type="button"
              >
                {tr('运维台（管理员）', 'Ops Console (Admin)')}
              </button>
            )}
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

        <main className="client-chat-shell">
          <aside className="panel client-sidebar">
            <div className="sidebar-head">
              <h2>{tr('会话', 'Conversations')}</h2>
              <div className="sidebar-head-actions">
                <button className="ghost" onClick={handleStartNewConversation} type="button">
                  {tr('新对话', 'New Chat')}
                </button>
                <button
                  className="ghost"
                  onClick={() => {
                    void refreshTasks()
                  }}
                  type="button"
                >
                  {refreshingTasks ? tr('刷新中...', 'Refreshing...') : tr('刷新会话', 'Refresh')}
                </button>
              </div>
            </div>

            <ul className="conversation-list">
              {clientConversations.map((conversation) => (
                <li key={conversation.id}>
                  <button
                    className={
                      conversation.id === selectedConversationID
                        ? 'conversation-item active'
                        : 'conversation-item'
                    }
                    onClick={() => handleSelectConversation(conversation.id)}
                    type="button"
                  >
                    <div className="conversation-row">
                      <strong>{conversation.title || tr('未命名对话', 'Untitled Chat')}</strong>
                      <span className={statusClass(conversation.latestTask.status)}>
                        {taskStatusLabel(conversation.latestTask.status)}
                      </span>
                    </div>
                    <p>{conversation.preview}</p>
                    <small>
                      {formatDateTime(conversation.latestTask.updated_at)} ·{' '}
                      {language === 'zh'
                        ? `${conversation.taskCount} 轮`
                        : `${conversation.taskCount} turns`}
                    </small>
                  </button>
                </li>
              ))}

              {clientConversations.length === 0 && (
                <li className="empty sidebar-empty">
                  {currentScopedUserID === ''
                    ? tr('当前账号不可用，请重新登录。', 'Current account is unavailable, please sign in again.')
                    : tr('还没有会话，点击上方“新对话”开始。', 'No conversation yet. Click New Chat above to begin.')}
                </li>
              )}
            </ul>
          </aside>

          <section className="panel client-chat-main">
            <div className="conversation-toolbar">
              <div>
                <h2>{activeConversationTitle}</h2>
                <p>
                  {selectedConversation
                    ? tr('同一会话支持连续提问，系统会自动带入最近上下文。', 'Keep chatting in this thread with recent context automatically included.')
                    : tr('输入消息并发送，系统会创建新会话。', 'Type a message and send to create a new chat thread.')}
                </p>
              </div>
              <span className={`stream-${streamState}`}>
                {streamStateLabel(streamState)} · #{lastEventID}
              </span>
            </div>

            <div className="chat-transcript" ref={clientTranscriptRef} onScroll={handleTranscriptScroll}>
              {conversationMessages.length > 0 ? (
                conversationMessages.map((message) => {
                  const isAssistant = message.role === 'assistant'
                  const showStreamingCaret =
                    isAssistant &&
                    !!activeConversationTask &&
                    message.taskID === activeConversationTask.id &&
                    isActiveAssistantStreaming

                  return (
                    <article
                      className={isAssistant ? 'chat-message chat-assistant' : 'chat-message chat-user'}
                      key={message.id}
                    >
                      <span className="chat-role">{isAssistant ? 'Synapse' : tr('你', 'You')}</span>
                      <div className={showStreamingCaret ? 'chat-bubble is-streaming' : 'chat-bubble'}>
                        <div className="chat-markdown">
                          <ChatMarkdown content={message.content} />
                        </div>
                        {showStreamingCaret && <span className="chat-caret" aria-hidden="true" />}
                      </div>
                      <div className="chat-meta">
                        <time>{formatDateTime(message.timestamp)}</time>
                        {isAssistant && (
                          <span className={statusClass(message.status)}>{taskStatusLabel(message.status)}</span>
                        )}
                      </div>
                    </article>
                  )
                })
              ) : (
                <p className="empty">
                  {tr(
                    '还没有消息，发送第一条内容开始聊天。',
                    'No messages yet. Send your first prompt to start chatting.',
                  )}
                </p>
              )}
            </div>

            <form className="client-composer" onSubmit={handleCreateTask}>
              <textarea
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                rows={4}
                placeholder={
                  currentScopedUserID === ''
                    ? tr('当前账号不可用，请重新登录。', 'Current account is unavailable, please sign in again.')
                    : tr('在这个会话里继续提问...', 'Continue chatting in this thread...')
                }
              />
              <div className="client-composer-foot">
                <span>
                  {selectedConversationID === NEW_CONVERSATION_DRAFT_ID
                    ? tr('将创建新会话', 'Will create a new chat')
                    : tr('将继续当前会话', 'Will continue current chat')}
                </span>
                <button disabled={submitting || currentScopedUserID === ''} type="submit">
                  {submitting ? tr('发送中...', 'Sending...') : tr('发送', 'Send')}
                </button>
              </div>
            </form>
          </section>
        </main>
      </div>
    )
  }

  if (!isAdmin) {
    return (
      <div className="app-shell">
        <header className="topbar">
          <div>
            <p className="eyebrow">{tr('访问受限', 'Access Restricted')}</p>
            <h1>{tr('运维台仅管理员可访问', 'Ops Console Is Admin Only')}</h1>
          </div>
          <div className="topbar-actions">
            <button className="mode-switch ghost" onClick={() => setViewMode('client')} type="button">
              {tr('返回用户端', 'Back To Client')}
            </button>
            <button className="ghost" onClick={handleLogout} type="button">
              {tr('退出登录', 'Sign Out')}
            </button>
          </div>
        </header>
        <section className="panel">
          <p className="empty">
            {tr(
              '请使用管理员账号登录后再进入运维台。',
              'Please sign in with an administrator account to access the ops console.',
            )}
          </p>
        </section>
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
          <div className="account-pill">
            <div>
              <strong>{currentUser.username}</strong>
              <span>{tr('管理员', 'admin')}</span>
            </div>
            <button className="ghost small" onClick={handleLogout} type="button">
              {tr('退出', 'Sign Out')}
            </button>
          </div>
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
            <section className="ops-user-list" aria-live="polite">
              <div className="ops-user-list-head">
                <h3>{tr('用户名单', 'User List')}</h3>
                <span>{knownUsers.length}</span>
              </div>
              <p className="ops-user-list-note">
                {tr(
                  '仅用于查看活跃用户，运维台创建任务将固定使用当前登录账号。',
                  'For visibility only. Ops task creation is locked to the current signed-in account.',
                )}
              </p>
              <ul className="ops-user-list-items">
                {knownUsers.map((user) => (
                  <li key={user}>{user}</li>
                ))}
                {knownUsers.length === 0 && (
                  <li className="ops-user-empty">{tr('暂无用户数据', 'No user data yet')}</li>
                )}
              </ul>
            </section>

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
            <span className={`stream-${streamState}`}>
              {streamStateLabel(streamState)} · #{lastEventID}
            </span>
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
