import { useMemo, useState } from 'react'
import type { Task } from '../../shared/types/domain'
import { approveTask } from '../tasks/api'
import {
  buildApprovalPayloadFromTask,
  getApprovalRequestSummary,
} from '../tasks/approval'

type Translate = (zh: string, en: string) => string

type ApprovalRequiredCardProps = {
  approved?: boolean
  canceling?: boolean
  currentUsername: string
  onApproved: (task: Task) => void | Promise<void>
  onCancel?: (taskID: string) => void | Promise<void>
  onError?: (message: string) => void
  task: Task
  tr: Translate
}

type ApprovalState = 'idle' | 'approving' | 'approved'

export function ApprovalRequiredCard({
  approved = false,
  canceling = false,
  currentUsername,
  onApproved,
  onCancel,
  onError,
  task,
  tr,
}: ApprovalRequiredCardProps) {
  const [approvalState, setApprovalState] = useState<ApprovalState>(
    approved ? 'approved' : 'idle',
  )
  const [error, setError] = useState('')
  const summary = useMemo(() => getApprovalRequestSummary(task, tr), [task, tr])
  const isApproved = approved || approvalState === 'approved'
  const isApproving = approvalState === 'approving'

  const handleApprove = async () => {
    if (isApproving || isApproved) {
      return
    }

    setApprovalState('approving')
    setError('')
    try {
      const resumedTask = await approveTask(
        task.id,
        buildApprovalPayloadFromTask(task, currentUsername, tr),
      )
      setApprovalState('approved')
      await onApproved(resumedTask)
    } catch (approvalError) {
      const message =
        approvalError instanceof Error
          ? approvalError.message
          : tr('审批恢复任务失败', 'Failed to approve and resume task')
      setApprovalState('idle')
      setError(message)
      onError?.(message)
    }
  }

  return (
    <section className="approval-required-card" aria-live="polite">
      <div className="approval-required-head">
        <span>{tr('审批请求', 'Approval Request')}</span>
        <strong>{summary.toolName}</strong>
      </div>

      <dl className="approval-required-grid">
        <div>
          <dt>{tr('风险等级', 'Risk Level')}</dt>
          <dd>{summary.riskLevel}</dd>
        </div>
        <div>
          <dt>{tr('当前状态', 'Current Status')}</dt>
          <dd>{isApproved ? tr('已批准，任务继续执行中', 'Approved. Task is resuming.') : summary.statusLabel}</dd>
        </div>
        <div className="approval-required-wide">
          <dt>{tr('工具输入', 'Tool Input')}</dt>
          <dd>{summary.toolInput || tr('未记录工具输入', 'No tool input captured')}</dd>
        </div>
        <div className="approval-required-wide">
          <dt>{tr('暂停原因', 'Pause Reason')}</dt>
          <dd>{summary.reason}</dd>
        </div>
      </dl>

      <div className="approval-required-actions">
        <button disabled={isApproving || isApproved} onClick={() => void handleApprove()} type="button">
          {isApproved
            ? tr('已批准', 'Approved')
            : isApproving
              ? tr('审批中...', 'Approving...')
              : tr('批准并继续', 'Approve And Continue')}
        </button>
        {onCancel && !isApproved && (
          <button
            className="ghost"
            disabled={isApproving || canceling}
            onClick={() => void onCancel(task.id)}
            type="button"
          >
            {canceling ? tr('取消中...', 'Canceling...') : tr('取消任务', 'Cancel Task')}
          </button>
        )}
      </div>

      {error && (
        <p className="approval-required-error" role="alert">
          {error}
        </p>
      )}
    </section>
  )
}
