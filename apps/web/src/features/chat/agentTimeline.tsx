import type { Language, StreamEvent, Task } from '../../shared/types/domain'
import { formatDateTime } from '../../shared/utils/format'
import { buildAgentTimelineItems } from './agentTimelineModel'

type Translate = (zh: string, en: string) => string

export function AgentTimeline({
  task,
  taskEvents,
  finalAnswer,
  language,
  tr,
}: {
  task: Task
  taskEvents: StreamEvent[]
  finalAnswer: string
  language: Language
  tr: Translate
}) {
  const items = buildAgentTimelineItems(task, taskEvents, finalAnswer, language, tr)
  if (items.length === 0) {
    return null
  }

  const hasAttentionItem = items.some((item) => item.status === 'warning' || item.status === 'error')

  return (
    <details className="agent-timeline" open={task.status !== 'completed' || hasAttentionItem}>
      <summary>
        <span>{tr('Agent 执行时间线', 'Agent Execution Timeline')}</span>
        <strong>{items.length}</strong>
      </summary>
      <ol>
        {items.map((item) => (
          <li className={`agent-timeline-item item-${item.kind} status-${item.status ?? 'neutral'}`} key={item.id}>
            <div className="timeline-marker" aria-hidden="true" />
            <div className="timeline-card">
              <div className="timeline-card-head">
                <strong>{item.title}</strong>
                {item.time && <time>{formatDateTime(item.time)}</time>}
              </div>
              {item.detail && <p>{item.detail}</p>}
              {item.meta.length > 0 && (
                <div className="timeline-meta">
                  {item.meta.map((meta) => (
                    <span key={meta}>{meta}</span>
                  ))}
                </div>
              )}
              {item.bullets.length > 0 && (
                <ul className="timeline-bullets">
                  {item.bullets.map((bullet, bulletIndex) => (
                    <li key={`${item.id}-bullet-${bulletIndex}`}>{bullet}</li>
                  ))}
                </ul>
              )}
              {item.links.length > 0 && (
                <div className="timeline-sources">
                  {item.links.map((link) => (
                    <a href={link.url} key={link.url} rel="noreferrer noopener" target="_blank">
                      {link.label}
                    </a>
                  ))}
                </div>
              )}
            </div>
          </li>
        ))}
      </ol>
    </details>
  )
}
