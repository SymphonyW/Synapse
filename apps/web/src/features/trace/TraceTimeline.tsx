import type { TraceStage } from './traceTypes'

type TraceTimelineProps = {
  stages: TraceStage[]
}

export function TraceTimeline({ stages }: TraceTimelineProps) {
  if (stages.length === 0) {
    return null
  }

  return (
    <nav className="trace-timeline" aria-label="Trace stages">
      {stages.map((stage) => (
        <a className={`trace-stage-link status-${stage.status}`} href={`#trace-${stage.id}`} key={stage.id}>
          <strong>{stage.title}</strong>
          {stage.subtitle && <span>{stage.subtitle}</span>}
        </a>
      ))}
    </nav>
  )
}
