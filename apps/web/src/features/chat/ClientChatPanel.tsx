import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import type { Language, SessionIdentity, Task } from '../../shared/types/domain'
import {
  CLIENT_CONVERSATION_ID_KEY,
  CLIENT_USER_MESSAGE_KEY,
  DEFAULT_APPROVED_TOOLS,
  NEW_CONVERSATION_DRAFT_ID,
} from '../../shared/utils/constants'
import { taskEventsForDisplay } from '../../shared/utils/events'
import { createConversationID, formatDateTime, statusClass, truncatePreview } from '../../shared/utils/format'
import { useTaskEvents } from '../../shared/hooks/useTaskEvents'
import { useTasks } from '../tasks/useTasks'
import { deleteConversation } from './api'
import { AgentTimeline } from './agentTimeline'
import { assistantTextForTask } from './agentTimelineModel'
import { ChatMarkdown } from './ChatMarkdown'

type Translate = (zh: string, en: string) => string

type ClientChatPanelProps = {
  currentUser: SessionIdentity
  language: Language
  tr: Translate
}

function taskStatusLabel(status: string | undefined, tr: Translate): string {
  switch (status) {
    case 'queued':
      return tr('排队中', 'queued')
    case 'running':
      return tr('执行中', 'running')
    case 'paused':
      return tr('已暂停', 'paused')
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

function streamStateLabel(state: string, tr: Translate): string {
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

export function ClientChatPanel({ currentUser, language, tr }: ClientChatPanelProps) {
  const tasks = useTasks({ tr })
  const [selectedConversationID, setSelectedConversationID] = useState('')
  const [deletingConversationID, setDeletingConversationID] = useState('')
  const [prompt, setPrompt] = useState('')
  const [agentEnabled, setAgentEnabled] = useState(true)
  const [memoryWriteEnabled, setMemoryWriteEnabled] = useState(true)
  const [approvalGranted, setApprovalGranted] = useState(false)
  const [approvedToolsInput, setApprovedToolsInput] = useState(DEFAULT_APPROVED_TOOLS)
  const [showClientComposerTools, setShowClientComposerTools] = useState(false)

  const clientTranscriptRef = useRef<HTMLDivElement | null>(null)
  const transcriptPinnedToBottomRef = useRef(true)
  const lastTranscriptConversationIDRef = useRef('')

  const myTasks = useMemo(
    () => tasks.tasks.filter((task) => task.user_id === currentUser.username),
    [currentUser.username, tasks.tasks],
  )

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
      ? clientConversations.find((conversation) => conversation.id === selectedConversationID) ?? null
      : null
  const activeConversationTask = selectedConversationTasks.at(-1) ?? null

  const taskEvents = useTaskEvents({
    enabled: true,
    selectedTaskID: tasks.selectedTaskID,
    hydrateTasks: selectedConversationTasks,
    onTerminal: tasks.fetchTask,
    onError: tasks.setRequestError,
    tr,
  })

  const isActiveAssistantStreaming =
    !!activeConversationTask &&
    (activeConversationTask.status === 'queued' ||
      activeConversationTask.status === 'running' ||
      taskEvents.streamState === 'connecting' ||
      taskEvents.streamState === 'live')
  const activeConversationTitle =
    selectedConversationID === NEW_CONVERSATION_DRAFT_ID
      ? tr('新对话', 'New Chat')
      : selectedConversation?.title || tr('会话', 'Conversation')

  const conversationMessages = useMemo(
    () =>
      selectedConversationTasks.flatMap((task) => {
        const userMessage = task.metadata?.[CLIENT_USER_MESSAGE_KEY] || task.prompt
        return [
          {
            id: `${task.id}-user`,
            role: 'user' as const,
            taskID: task.id,
            task,
            content: userMessage,
            timestamp: task.created_at,
            status: task.status,
          },
          {
            id: `${task.id}-assistant`,
            role: 'assistant' as const,
            taskID: task.id,
            task,
            content: assistantTextForTask(task, taskEvents.responseByTaskID, tr),
            timestamp: task.updated_at,
            status: task.status,
          },
        ]
      }),
    [selectedConversationTasks, taskEvents.responseByTaskID, tr],
  )

  useEffect(() => {
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
  }, [clientConversations, selectedConversationID])

  useEffect(() => {
    if (!activeConversationTask) {
      if (tasks.selectedTaskID) {
        tasks.setSelectedTaskID('')
      }
      return
    }
    if (tasks.selectedTaskID !== activeConversationTask.id) {
      tasks.setSelectedTaskID(activeConversationTask.id)
    }
  }, [activeConversationTask, tasks])

  useEffect(() => {
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
  }, [conversationMessages, selectedConversationID])

  const handleTranscriptScroll = () => {
    const container = clientTranscriptRef.current
    if (!container) {
      return
    }
    const distanceToBottom = container.scrollHeight - container.scrollTop - container.clientHeight
    transcriptPinnedToBottomRef.current = distanceToBottom <= 44
  }

  const handleSelectConversation = (conversationID: string) => {
    tasks.setRequestError('')
    transcriptPinnedToBottomRef.current = true
    setSelectedConversationID(conversationID)
  }

  const handleStartNewConversation = () => {
    tasks.setRequestError('')
    transcriptPinnedToBottomRef.current = true
    setSelectedConversationID(NEW_CONVERSATION_DRAFT_ID)
    tasks.setSelectedTaskID('')
    setPrompt('')
  }

  const handleDeleteConversation = async (conversationID: string) => {
    if (conversationID.trim() === '' || deletingConversationID !== '') {
      return
    }

    const targetConversation = clientConversations.find((conversation) => conversation.id === conversationID)
    const confirmMessage =
      language === 'zh'
        ? `确认删除会话“${targetConversation?.title || '未命名对话'}”吗？该会话的全部记录会被删除且不可恢复。`
        : `Delete conversation "${targetConversation?.title || 'Untitled Chat'}"? All records in this conversation will be removed permanently.`
    if (!window.confirm(confirmMessage)) {
      return
    }

    setDeletingConversationID(conversationID)
    tasks.setRequestError('')
    try {
      const response = await deleteConversation(conversationID)
      if (response.deleted_task_ids.length === 0) {
        return
      }
      tasks.removeTasks(response.deleted_task_ids)
      taskEvents.removeTasks(response.deleted_task_ids)
      if (selectedConversationID === conversationID) {
        transcriptPinnedToBottomRef.current = true
        setSelectedConversationID('')
      }
      await tasks.refreshTasks()
    } catch (error) {
      tasks.setRequestError(
        error instanceof Error
          ? error.message
          : tr('删除会话失败', 'Failed to delete conversation'),
      )
    } finally {
      setDeletingConversationID('')
    }
  }

  const handleCreateTask = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const messageInput = prompt.trim()
    if (!messageInput) {
      tasks.setRequestError(tr('prompt 不能为空', 'prompt is required'))
      return
    }

    const nextConversationID =
      selectedConversationID && selectedConversationID !== NEW_CONVERSATION_DRAFT_ID
        ? selectedConversationID
        : createConversationID()
    const metadata: Record<string, string> = {
      source: 'web-console',
      agent_enabled: agentEnabled ? 'true' : 'false',
      memory_write_enabled: memoryWriteEnabled ? 'true' : 'false',
      client_view: 'chat',
      [CLIENT_CONVERSATION_ID_KEY]: nextConversationID,
      [CLIENT_USER_MESSAGE_KEY]: messageInput,
    }
    if (approvalGranted) {
      metadata.approval_granted = 'true'
    }
    const approvedTools = approvedToolsInput
      .split(',')
      .map((item) => item.trim())
      .filter((item) => item.length > 0)
    if (approvedTools.length > 0) {
      metadata.approved_tools = approvedTools.join(',')
    }

    const created = await tasks.create({
      user_id: currentUser.username,
      prompt: messageInput,
      metadata,
    })
    if (!created) {
      return
    }

    taskEvents.prepareTask(created.id)
    transcriptPinnedToBottomRef.current = true
    setSelectedConversationID(nextConversationID)
    setPrompt('')
  }

  return (
    <>
      {tasks.requestError && <p className="error-banner">{tasks.requestError}</p>}

      <main className="client-chat-shell">
        <aside className="panel client-sidebar">
          <div className="sidebar-head">
            <h2>{tr('会话', 'Conversations')}</h2>
            <div className="sidebar-head-actions">
              <button className="ghost" onClick={handleStartNewConversation} type="button">
                {tr('新对话', 'New Chat')}
              </button>
              <button className="ghost" onClick={() => void tasks.refreshTasks()} type="button">
                {tasks.refreshingTasks ? tr('刷新中...', 'Refreshing...') : tr('刷新会话', 'Refresh')}
              </button>
            </div>
          </div>

          <ul className="conversation-list">
            {clientConversations.map((conversation) => (
              <li key={conversation.id}>
                <article className={conversation.id === selectedConversationID ? 'conversation-item active' : 'conversation-item'}>
                  <button
                    className="conversation-main"
                    disabled={deletingConversationID === conversation.id}
                    onClick={() => handleSelectConversation(conversation.id)}
                    type="button"
                  >
                    <div className="conversation-row">
                      <strong>{conversation.title || tr('未命名对话', 'Untitled Chat')}</strong>
                      <span className={statusClass(conversation.latestTask.status)}>
                        {taskStatusLabel(conversation.latestTask.status, tr)}
                      </span>
                    </div>
                    <p>{conversation.preview}</p>
                    <small>
                      {formatDateTime(conversation.latestTask.updated_at)} ·{' '}
                      {language === 'zh' ? `${conversation.taskCount} 轮` : `${conversation.taskCount} turns`}
                    </small>
                  </button>
                  <div className="conversation-actions">
                    <button
                      className="ghost small conversation-delete"
                      disabled={deletingConversationID === conversation.id}
                      onClick={() => void handleDeleteConversation(conversation.id)}
                      type="button"
                    >
                      {deletingConversationID === conversation.id ? tr('删除中...', 'Deleting...') : tr('删除', 'Delete')}
                    </button>
                  </div>
                </article>
              </li>
            ))}

            {clientConversations.length === 0 && (
              <li className="empty sidebar-empty">
                {tr('还没有会话，点击上方“新对话”开始。', 'No conversation yet. Click New Chat above to begin.')}
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
                  ? tr(
                      '同一会话支持连续提问，系统会自动带入最近上下文。',
                      'Keep chatting in this thread with recent context automatically included.',
                    )
                  : tr('输入消息并发送，系统会创建新会话。', 'Type a message and send to create a new chat thread.')}
              </p>
            </div>
            <span className={`stream-${taskEvents.streamState}`}>
              {streamStateLabel(taskEvents.streamState, tr)} · #{taskEvents.lastEventID}
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
                const timelineEvents = taskEventsForDisplay(
                  taskEvents.eventsByTaskID,
                  taskEvents.events,
                  message.taskID,
                  tasks.selectedTaskID,
                )

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
                        <span className={statusClass(message.status)}>
                          {taskStatusLabel(message.status, tr)}
                        </span>
                      )}
                    </div>
                    {isAssistant && (
                      <AgentTimeline
                        finalAnswer={taskEvents.responseByTaskID[message.taskID] ?? ''}
                        language={language}
                        task={message.task}
                        taskEvents={timelineEvents}
                        tr={tr}
                      />
                    )}
                  </article>
                )
              })
            ) : (
              <p className="empty">
                {tr('还没有消息，发送第一条内容开始聊天。', 'No messages yet. Send your first prompt to start chatting.')}
              </p>
            )}
          </div>

          <form className="client-composer" onSubmit={handleCreateTask}>
            <div className="client-composer-head">
              <button
                aria-controls="client-agent-controls"
                aria-expanded={showClientComposerTools}
                className="ghost small composer-settings-toggle"
                onClick={() => setShowClientComposerTools((previous) => !previous)}
                type="button"
              >
                {showClientComposerTools ? tr('收起高级设置', 'Hide advanced settings') : tr('展开高级设置', 'Show advanced settings')}
              </button>
            </div>
            {showClientComposerTools && (
              <div className="agent-controls agent-controls-client" aria-live="polite" id="client-agent-controls">
                <label className="agent-toggle">
                  <input checked={agentEnabled} onChange={(event) => setAgentEnabled(event.target.checked)} type="checkbox" />
                  {tr('启用 Agent 规划循环', 'Enable agent planning loop')}
                </label>
                <label className="agent-toggle">
                  <input checked={memoryWriteEnabled} onChange={(event) => setMemoryWriteEnabled(event.target.checked)} type="checkbox" />
                  {tr('写入长期记忆', 'Write long-term memory')}
                </label>
                <label className="agent-toggle">
                  <input checked={approvalGranted} onChange={(event) => setApprovalGranted(event.target.checked)} type="checkbox" />
                  {tr('预授权高风险工具', 'Pre-approve high-risk tools')}
                </label>
                <input
                  value={approvedToolsInput}
                  onChange={(event) => setApprovedToolsInput(event.target.value)}
                  placeholder={tr('授权工具列表（逗号分隔）', 'Approved tools (comma-separated)')}
                />
              </div>
            )}
            <textarea
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              rows={4}
              placeholder={tr('在这个会话里继续提问...', 'Continue chatting in this thread...')}
            />
            <div className="client-composer-foot">
              <span>
                {selectedConversationID === NEW_CONVERSATION_DRAFT_ID
                  ? tr('将创建新会话', 'Will create a new chat')
                  : tr('将继续当前会话', 'Will continue current chat')}
              </span>
              <button disabled={tasks.submitting} type="submit">
                {tasks.submitting ? tr('发送中...', 'Sending...') : tr('发送', 'Send')}
              </button>
            </div>
          </form>
        </section>
      </main>
    </>
  )
}
