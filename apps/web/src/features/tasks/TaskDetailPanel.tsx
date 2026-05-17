import { ReplayDiffPanel } from '../trace/ReplayDiffPanel'
import { TraceWorkbench } from '../trace/TraceWorkbench'
import type { Language, StreamEvent, Task } from '../../shared/types/domain'
import type { ReplayComparePayload } from '../trace/ReplayDiffPanel'
import { statusClass } from '../../shared/utils/format'

type Translate = (zh: string, en: string) => string

type TaskDetailPanelProps = {
  approvingTaskID: string
  cancelingTaskID: string
  events: StreamEvent[]
  language: Language
  lastEventID: number
  loadingReplayCompare: boolean
  loadingTaskReplays: boolean
  onApprove: (taskID: string) => void
  onCancel: (taskID: string) => void
  onCloseCompare: () => void
  onCompareReplay: (replayTaskID: string) => void
  onRefreshReplays: () => void
  replayCompareData: ReplayComparePayload | null
  replayCompareError: string
  selectedTask: Task | undefined
  streamState: string
  taskReplays: Task[]
  taskReplaysLoaded: boolean
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

export function TaskDetailPanel({
  approvingTaskID,
  cancelingTaskID,
  events,
  language,
  lastEventID,
  loadingReplayCompare,
  loadingTaskReplays,
  onApprove,
  onCancel,
  onCloseCompare,
  onCompareReplay,
  onRefreshReplays,
  replayCompareData,
  replayCompareError,
  selectedTask,
  streamState,
  taskReplays,
  taskReplaysLoaded,
  tr,
}: TaskDetailPanelProps) {
  return (
    <section className="panel panel-stream">
      <div className="panel-head">
        <h2>{tr('Agent Trace 工作台', 'Agent Trace Workbench')}</h2>
        <span className={`stream-${streamState}`}>
          {streamStateLabel(streamState, tr)} · #{lastEventID}
        </span>
      </div>

      {selectedTask ? (
        <div className="selected-meta">
          <span className="selected-task-id">{selectedTask.id}</span>
          {selectedTask.status === 'paused' && (
            <div className="approval-callout">
              <strong>{tr('审批请求', 'Approval Request')}</strong>
              <span>{(selectedTask.metadata?.agent_required_tool ?? '').trim() || tr('未知工具', 'Unknown tool')}</span>
              {(selectedTask.metadata?.agent_required_tool_input ?? '').trim() && (
                <small>{selectedTask.metadata?.agent_required_tool_input}</small>
              )}
            </div>
          )}
          <div className="selected-actions">
            <span className={statusClass(selectedTask.status)}>{taskStatusLabel(selectedTask.status, tr)}</span>
            {selectedTask.status === 'paused' && (
              <button disabled={approvingTaskID === selectedTask.id} onClick={() => onApprove(selectedTask.id)} type="button">
                {approvingTaskID === selectedTask.id ? tr('恢复中...', 'Resuming...') : tr('审批并恢复', 'Approve & Resume')}
              </button>
            )}
            {(selectedTask.status === 'queued' ||
              selectedTask.status === 'running' ||
              selectedTask.status === 'paused') && (
              <button
                className="danger"
                disabled={cancelingTaskID === selectedTask.id}
                onClick={() => onCancel(selectedTask.id)}
                type="button"
              >
                {cancelingTaskID === selectedTask.id ? tr('取消中...', 'Canceling...') : tr('取消', 'Cancel')}
              </button>
            )}
          </div>
        </div>
      ) : (
        <p className="empty">{tr('选择一个任务以查看事件流。', 'Select a task to stream events.')}</p>
      )}

      {selectedTask ? (
        <TraceWorkbench
          events={events}
          language={language}
          task={{
            id: selectedTask.id,
            conversationId: selectedTask.metadata?.conversation_id,
            status: selectedTask.status,
            prompt: selectedTask.prompt,
            userId: selectedTask.user_id,
            createdAt: selectedTask.created_at,
            updatedAt: selectedTask.updated_at,
            error: selectedTask.error,
          }}
        />
      ) : (
        <p className="empty">{tr('暂无事件。', 'No events yet.')}</p>
      )}

      {selectedTask && (
        <ReplayDiffPanel
          compareData={replayCompareData}
          error={replayCompareError}
          language={language}
          loadingCompare={loadingReplayCompare}
          loadingReplays={loadingTaskReplays}
          onCloseCompare={onCloseCompare}
          onCompareReplay={onCompareReplay}
          onRefreshReplays={onRefreshReplays}
          replays={taskReplays}
          replaysLoaded={taskReplaysLoaded}
        />
      )}
    </section>
  )
}
