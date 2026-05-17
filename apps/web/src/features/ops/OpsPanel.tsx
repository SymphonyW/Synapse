import { useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import type {
  ApprovedToolCallPayload,
  Language,
  SessionIdentity,
  Task,
} from '../../shared/types/domain'
import {
  DEFAULT_APPROVED_TOOLS,
  TASK_STATUS_ORDER,
} from '../../shared/utils/constants'
import { formatDateTime } from '../../shared/utils/format'
import { taskEventsForDisplay } from '../../shared/utils/events'
import { useTaskEvents } from '../../shared/hooks/useTaskEvents'
import { TaskDetailPanel } from '../tasks/TaskDetailPanel'
import { TaskListPanel } from '../tasks/TaskListPanel'
import { useTasks } from '../tasks/useTasks'
import { useDeadLetters } from './useDeadLetters'

type Translate = (zh: string, en: string) => string

type OpsPanelProps = {
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

function buildApprovedToolCallFromTask(task: Task, tr: Translate): ApprovedToolCallPayload | null {
  const metadata = task.metadata ?? {}
  const toolName = (metadata.agent_required_tool ?? '').trim()
  const toolInput = (metadata.agent_required_tool_input ?? '').trim()
  if (toolName === '' || toolInput === '') {
    return null
  }

  const resumeStepIndex = Number.parseInt(metadata.agent_resume_step_index ?? '', 10)
  return {
    tool_name: toolName,
    tool_input: toolInput,
    risk_level: (metadata.agent_required_tool_risk_level ?? '').trim(),
    reason:
      (metadata.agent_required_reason ?? '').trim() ||
      tr('人工审批通过并恢复任务', 'Task approved and resumed by operator'),
    resume_step_index: Number.isFinite(resumeStepIndex) ? resumeStepIndex : 0,
  }
}

export function OpsPanel({ currentUser, language, tr }: OpsPanelProps) {
  const tasks = useTasks({ tr })
  const deadLetters = useDeadLetters({ enabled: currentUser.role === 'admin', tr })
  const [prompt, setPrompt] = useState('')
  const [cancelReason, setCancelReason] = useState(language === 'zh' ? '手动停止' : 'manual stop')
  const [agentEnabled, setAgentEnabled] = useState(true)
  const [memoryWriteEnabled, setMemoryWriteEnabled] = useState(true)
  const [approvalGranted, setApprovalGranted] = useState(false)
  const [approvedToolsInput, setApprovedToolsInput] = useState(DEFAULT_APPROVED_TOOLS)

  const taskEvents = useTaskEvents({
    enabled: true,
    selectedTaskID: tasks.selectedTaskID,
    onTerminal: async (taskID) => {
      await tasks.fetchTask(taskID)
      await deadLetters.refreshDeadLetters()
    },
    onError: tasks.setRequestError,
    tr,
  })

  const knownUsers = useMemo(() => {
    const latestByUser = new Map<string, number>()
    tasks.tasks.forEach((task) => {
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
  }, [tasks.tasks])

  const opsStatusCounts = useMemo(
    () =>
      TASK_STATUS_ORDER.map((status) => ({
        status,
        count: tasks.tasks.filter((task) => task.status === status).length,
      })),
    [tasks.tasks],
  )

  const selectedTaskEvents = useMemo(
    () =>
      tasks.selectedTaskID
        ? taskEventsForDisplay(
            taskEvents.eventsByTaskID,
            taskEvents.events,
            tasks.selectedTaskID,
            tasks.selectedTaskID,
          )
        : [],
    [taskEvents.events, taskEvents.eventsByTaskID, tasks.selectedTaskID],
  )

  const handleCreateTask = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const messageInput = prompt.trim()
    if (!messageInput) {
      tasks.setRequestError(tr('prompt 不能为空', 'prompt is required'))
      return
    }

    const metadata: Record<string, string> = {
      source: 'web-console',
      agent_enabled: agentEnabled ? 'true' : 'false',
      memory_write_enabled: memoryWriteEnabled ? 'true' : 'false',
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
    setPrompt('')
  }

  const handleReplay = async (taskID: string) => {
    const replayed = await tasks.replay(taskID)
    if (!replayed) {
      return
    }
    taskEvents.prepareTask(replayed.id)
    await deadLetters.refreshDeadLetters()
  }

  const handleApprove = async (taskID: string) => {
    const approvedTools = approvedToolsInput
      .split(',')
      .map((item) => item.trim())
      .filter((item) => item.length > 0)
    const taskToApprove = tasks.tasks.find((task) => task.id === taskID)
    const approvedToolCall = taskToApprove ? buildApprovedToolCallFromTask(taskToApprove, tr) : null
    await tasks.approve(taskID, {
      requested_by: currentUser.username,
      reason: tr('人工审批通过并恢复任务', 'Task approved and resumed by operator'),
      ...(approvedToolCall ? { approved_tool_call: approvedToolCall } : { approved_tools: approvedTools }),
    })
    await deadLetters.refreshDeadLetters()
  }

  const handleCancel = async (taskID: string) => {
    await tasks.cancel(taskID, {
      requested_by: currentUser.username,
      reason: cancelReason.trim(),
    })
    await deadLetters.refreshDeadLetters()
  }

  const handleBatchCancel = async () => {
    await tasks.batchCancel({
      requested_by: currentUser.username,
      reason: cancelReason.trim(),
    })
    await deadLetters.refreshDeadLetters()
  }

  return (
    <>
      {(tasks.requestError || deadLetters.error) && (
        <p className="error-banner">{tasks.requestError || deadLetters.error}</p>
      )}

      <section className="ops-overview" aria-live="polite">
        {opsStatusCounts.map((item) => (
          <article className="ops-metric" key={item.status}>
            <span>{taskStatusLabel(item.status, tr)}</span>
            <strong>{item.count}</strong>
          </article>
        ))}
        <article className="ops-metric ops-metric-dead">
          <span>{tr('死信', 'Dead Letters')}</span>
          <strong>{deadLetters.deadLetters.length}</strong>
        </article>
        <article className="ops-metric">
          <span>{tr('已选', 'Selected')}</span>
          <strong>{tasks.selectedCancelableTaskIDs.length}</strong>
        </article>
      </section>

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

            <div className="agent-controls" aria-live="polite">
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

            <button disabled={tasks.submitting} type="submit">
              {tasks.submitting ? tr('提交中...', 'Submitting...') : tr('加入队列', 'Queue Task')}
            </button>
          </form>
        </section>

        <TaskListPanel
          batchCancelHistory={tasks.batchCancelHistory}
          bulkCanceling={tasks.bulkCanceling}
          cancelReason={cancelReason}
          copyFeedback={tasks.copyFeedback}
          expandedBatchResultIDs={tasks.expandedBatchResultIDs}
          language={language}
          onBatchCancel={() => void handleBatchCancel()}
          onChangeCancelReason={setCancelReason}
          onChangeFilter={tasks.setTaskStatusFilter}
          onCopyFailedTaskIDs={(result) => void tasks.copyFailedTaskIDs(result)}
          onRefresh={() => void tasks.refreshTasks()}
          onSelectTask={tasks.setSelectedTaskID}
          onToggleBatchResult={tasks.toggleBatchResultExpanded}
          onToggleSelectAll={tasks.toggleSelectAllCancelable}
          onToggleTask={tasks.toggleTaskSelection}
          refreshingTasks={tasks.refreshingTasks}
          selectedCancelableTaskIDs={tasks.selectedCancelableTaskIDs}
          selectedTaskID={tasks.selectedTaskID}
          selectedTaskIDs={tasks.selectedTaskIDs}
          taskStatusFilter={tasks.taskStatusFilter}
          tasks={tasks.tasks}
          tr={tr}
        />

        <TaskDetailPanel
          approvingTaskID={tasks.approvingTaskID}
          cancelingTaskID={tasks.cancelingTaskID}
          events={selectedTaskEvents}
          language={language}
          lastEventID={taskEvents.lastEventID}
          loadingReplayCompare={tasks.loadingReplayCompare}
          loadingTaskReplays={tasks.loadingTaskReplays}
          onApprove={(taskID) => void handleApprove(taskID)}
          onCancel={(taskID) => void handleCancel(taskID)}
          onCloseCompare={() => tasks.setReplayCompareData(null)}
          onCompareReplay={(replayTaskID) => {
            if (tasks.selectedTask) {
              void tasks.compareReplayTask(tasks.selectedTask.id, replayTaskID)
            }
          }}
          onRefreshReplays={() => {
            if (tasks.selectedTask) {
              void tasks.refreshTaskReplays(tasks.selectedTask.id)
            }
          }}
          replayCompareData={tasks.replayCompareData}
          replayCompareError={tasks.replayCompareError}
          selectedTask={tasks.selectedTask}
          streamState={taskEvents.streamState}
          taskReplays={tasks.taskReplays}
          taskReplaysLoaded={tasks.taskReplaysLoaded}
          tr={tr}
        />

        <section className="panel panel-dead">
          <div className="panel-head">
            <h2>{tr('死信任务', 'Dead Letters')}</h2>
            <button className="ghost" onClick={() => void deadLetters.refreshDeadLetters()} type="button">
              {deadLetters.refreshingDeadLetters ? tr('刷新中...', 'Refreshing...') : tr('刷新', 'Refresh')}
            </button>
          </div>

          <ul className="dead-list">
            {deadLetters.deadLetters.map((entry) => (
              <li key={entry.task_id} className="dead-item">
                <div>
                  <p>{entry.task_id}</p>
                  <small>{entry.reason}</small>
                </div>
                <div className="dead-actions">
                  <span>{tr('尝试次数', 'attempts')}: {entry.attempts}</span>
                  <button
                    disabled={tasks.replayingTaskID === entry.task_id}
                    onClick={() => void handleReplay(entry.task_id)}
                    type="button"
                  >
                    {tasks.replayingTaskID === entry.task_id ? tr('重放中...', 'Replaying...') : tr('重放', 'Replay')}
                  </button>
                </div>
              </li>
            ))}
            {deadLetters.deadLetters.length === 0 && <li className="empty">{tr('暂无死信任务。', 'No dead letters.')}</li>}
          </ul>
          <div className="panel-footnote">
            <small>{tr('更新时间', 'Updated')}: {formatDateTime(deadLetters.lastUpdatedAt ?? undefined)}</small>
          </div>
        </section>
      </main>
    </>
  )
}
