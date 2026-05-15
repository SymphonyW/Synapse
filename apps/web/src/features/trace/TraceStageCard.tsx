import type { ReactNode } from 'react'

type TraceStageCardProps = {
  id: string
  title: string
  subtitle?: string
  tone?: 'neutral' | 'ok' | 'warning' | 'error'
  defaultOpen?: boolean
  children: ReactNode
}

export function TraceStageCard({
  id,
  title,
  subtitle,
  tone = 'neutral',
  defaultOpen = true,
  children,
}: TraceStageCardProps) {
  return (
    <details className={`trace-stage-card tone-${tone}`} id={`trace-${id}`} open={defaultOpen}>
      <summary>
        <strong>{title}</strong>
        {subtitle && <span>{subtitle}</span>}
      </summary>
      <div className="trace-stage-body">{children}</div>
    </details>
  )
}
