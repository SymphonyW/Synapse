import type { BatchCancelResult, Language, Task, TaskStatus } from '../../shared/types/domain'
import { formatDateTime, statusClass } from '../../shared/utils/format'
import { isCancelableTask } from './useTasks'

type Translate = (zh: string, en: string) => string

type TaskListPanelProps = {
  batchCancelHistory: BatchCancelResult[]
  bulkCanceling: boolean
  cancelReason: string
  copyFeedback: { resultID: string; state: 'copied' | 'failed' } | null
  expandedBatchResultIDs: string[]
  language: Language
  onBatchCancel: () => void
  onChangeCancelReason: (value: string) => void
  onChangeFilter: (value: 'all' | TaskStatus) => void
  onCopyFailedTaskIDs: (result: BatchCancelResult) => void
  onRefresh: () => void
  onSelectTask: (taskID: string) => void
  onToggleBatchResult: (resultID: string) => void
  onToggleSelectAll: (checked: boolean) => void
  onToggleTask: (taskID: string, checked: boolean) => void
  refreshingTasks: boolean
  selectedCancelableTaskIDs: string[]
  selectedTaskID: string
  selectedTaskIDs: string[]
  taskStatusFilter: 'all' | TaskStatus
  tasks: Task[]
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

export function TaskListPanel({
  batchCancelHistory,
  bulkCanceling,
  cancelReason,
  copyFeedback,
  expandedBatchResultIDs,
  language,
  onBatchCancel,
  onChangeCancelReason,
  onChangeFilter,
  onCopyFailedTaskIDs,
  onRefresh,
  onSelectTask,
  onToggleBatchResult,
  onToggleSelectAll,
  onToggleTask,
  refreshingTasks,
  selectedCancelableTaskIDs,
  selectedTaskID,
  selectedTaskIDs,
  taskStatusFilter,
  tasks,
  tr,
}: TaskListPanelProps) {
  return (
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
              onChange={(event) => onToggleSelectAll(event.target.checked)}
            />
            {tr('全选', 'all')}
          </label>
          <select
            aria-label={tr('按状态过滤任务', 'filter tasks by status')}
            value={taskStatusFilter}
            onChange={(event) => onChangeFilter(event.target.value as 'all' | TaskStatus)}
          >
            <option value="all">{tr('全部', 'all')}</option>
            <option value="queued">{tr('排队中', 'queued')}</option>
            <option value="running">{tr('执行中', 'running')}</option>
            <option value="paused">{tr('已暂停', 'paused')}</option>
            <option value="completed">{tr('已完成', 'completed')}</option>
            <option value="failed">{tr('失败', 'failed')}</option>
            <option value="canceled">{tr('已取消', 'canceled')}</option>
          </select>
          <button className="ghost" onClick={onRefresh} type="button">
            {refreshingTasks ? tr('刷新中...', 'Refreshing...') : tr('刷新', 'Refresh')}
          </button>
          <span>{tasks.length}</span>
        </div>
      </div>

      <div className="batch-toolbar">
        <input
          aria-label={tr('取消原因', 'cancellation reason')}
          value={cancelReason}
          onChange={(event) => onChangeCancelReason(event.target.value)}
          placeholder={tr('取消原因', 'cancel reason')}
        />
        <button
          className="danger"
          disabled={bulkCanceling || selectedCancelableTaskIDs.length === 0}
          onClick={onBatchCancel}
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
              const copyState = copyFeedback?.resultID === result.id ? copyFeedback.state : null
              return (
                <li key={result.id} className={expanded ? 'batch-result expanded' : 'batch-result'}>
                  <button className="batch-result-toggle" onClick={() => onToggleBatchResult(result.id)} type="button">
                    <div className="batch-result-head">
                      <h4>{formatDateTime(result.generated_at_unix_ms)}</h4>
                      <span>{expanded ? tr('收起', 'Collapse') : tr('展开', 'Expand')}</span>
                    </div>
                    <div className="batch-metrics">
                      <span>{tr('请求数', 'requested')}: {result.response.requested}</span>
                      <span>{tr('处理数', 'processed')}: {result.response.canceled_count}</span>
                      <span>{tr('已取消', 'already canceled')}: {result.response.already_canceled_count}</span>
                      <span>{tr('失败数', 'failed')}: {result.response.failed_count}</span>
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
                              <em>{taskStatusLabel(task.status, tr)}</em>
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
                              onClick={() => onCopyFailedTaskIDs(result)}
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
                onChange={(event) => onToggleTask(task.id, event.target.checked)}
                disabled={!isCancelableTask(task)}
              />
            </label>
            <button
              className={task.id === selectedTaskID ? 'task-item active' : 'task-item'}
              onClick={() => onSelectTask(task.id)}
              type="button"
            >
              <div>
                <p>{task.prompt}</p>
                <small>{task.id.slice(0, 8)}</small>
              </div>
              <span className={statusClass(task.status)}>{taskStatusLabel(task.status, tr)}</span>
            </button>
          </li>
        ))}
        {tasks.length === 0 && (
          <li className="empty">{tr('当前还没有任务，先创建一个吧。', 'No tasks yet. Create one to begin.')}</li>
        )}
      </ul>
    </section>
  )
}
