import type { ToolCatalogItem } from '../../shared/types/domain'

type Translate = (zh: string, en: string) => string

type ToolPreauthorizationPanelProps = {
  error: string
  loading: boolean
  onClear: () => void
  onToggleTool: (toolName: string) => void
  selectedTools: string[]
  tools: ToolCatalogItem[]
  tr: Translate
}

function isHighRiskTool(tool: ToolCatalogItem): boolean {
  const riskLevel = tool.risk_level.toLowerCase()
  return tool.requires_approval || riskLevel === 'high' || riskLevel === 'critical'
}

function selectedCountLabel(count: number, tr: Translate): string {
  if (count === 1) {
    return tr('已选择 1 个工具', 'Selected 1 tool')
  }
  return tr(`已选择 ${count} 个工具`, `Selected ${count} tools`)
}

function disabledReasonLabel(tool: ToolCatalogItem, tr: Translate): string {
  if (tool.currently_disabled || tool.disabled_reason === 'disabled') {
    return tr('已禁用', 'disabled')
  }
  if (!tool.allowed_for_role || tool.disabled_reason === 'not_allowed_for_role') {
    return tr('当前角色不可用', 'not allowed')
  }
  return ''
}

function ToolGroup({
  title,
  note,
  tools,
  selectedTools,
  onToggleTool,
  tr,
}: {
  title: string
  note: string
  tools: ToolCatalogItem[]
  selectedTools: string[]
  onToggleTool: (toolName: string) => void
  tr: Translate
}) {
  if (tools.length === 0) {
    return null
  }

  return (
    <section className="tool-picker-group">
      <div className="tool-picker-group-head">
        <strong>{title}</strong>
        <span>{note}</span>
      </div>
      <div className="tool-picker-grid">
        {tools.map((tool) => {
          const selected = selectedTools.includes(tool.name)
          const unavailableReason = disabledReasonLabel(tool, tr)
          return (
            <button
              aria-pressed={selected}
              className={`tool-picker-chip risk-${tool.risk_level.toLowerCase()}${selected ? ' selected' : ''}`}
              disabled={!tool.selectable}
              key={tool.name}
              onClick={() => onToggleTool(tool.name)}
              type="button"
            >
              <span className="tool-picker-name">{tool.name}</span>
              <span className={`tool-risk-badge risk-${tool.risk_level.toLowerCase()}`}>
                {tool.risk_level}
              </span>
              {tool.requires_approval && <span className="tool-policy-badge">{tr('需审批', 'approval')}</span>}
              {unavailableReason && <span className="tool-policy-badge muted">{unavailableReason}</span>}
            </button>
          )
        })}
      </div>
    </section>
  )
}

export function ToolPreauthorizationPanel({
  error,
  loading,
  onClear,
  onToggleTool,
  selectedTools,
  tools,
  tr,
}: ToolPreauthorizationPanelProps) {
  const highRiskTools = tools.filter(isHighRiskTool)
  const otherTools = tools.filter((tool) => !isHighRiskTool(tool) && tool.selectable)
  const unavailableTools = tools.filter((tool) => !isHighRiskTool(tool) && !tool.selectable)

  return (
    <section className="tool-picker-panel" aria-live="polite">
      <div className="tool-picker-copy">
        <strong>{tr('工具预授权', 'Tool Pre-Authorization')}</strong>
        <p>
          {tr(
            '仅对本次新任务写入所选工具名；真正放行仍由后端 ToolPolicy 与运行时审批匹配裁决。',
            'Selected tools are written only for the next task; ToolPolicy and runtime approval matching still decide execution.',
          )}
        </p>
      </div>

      {loading && <p className="tool-picker-note">{tr('正在加载工具目录...', 'Loading tool catalog...')}</p>}
      {error && <p className="tool-picker-error" role="alert">{error}</p>}
      {!loading && !error && tools.length === 0 && (
        <p className="tool-picker-note">{tr('暂无可显示工具。', 'No tools available.')}</p>
      )}

      {!loading && !error && tools.length > 0 && (
        <>
          <ToolGroup
            note={tr('默认建议只预授权这些会触发暂停的工具', 'Recommended for tools that may pause execution')}
            onToggleTool={onToggleTool}
            selectedTools={selectedTools}
            title={tr('高风险或需审批', 'High Risk Or Approval Required')}
            tools={highRiskTools}
            tr={tr}
          />
          <ToolGroup
            note={tr('用于兼容旧流程，通常不需要预授权', 'For compatibility; usually does not need pre-approval')}
            onToggleTool={onToggleTool}
            selectedTools={selectedTools}
            title={tr('其他可选工具', 'Other Selectable Tools')}
            tools={otherTools}
            tr={tr}
          />
          <ToolGroup
            note={tr('仅展示状态，不能选择', 'Status only; cannot be selected')}
            onToggleTool={onToggleTool}
            selectedTools={selectedTools}
            title={tr('不可用工具', 'Unavailable Tools')}
            tools={unavailableTools}
            tr={tr}
          />
        </>
      )}

      <div className="tool-picker-footer">
        <span>{selectedCountLabel(selectedTools.length, tr)}</span>
        <button className="ghost small" disabled={selectedTools.length === 0} onClick={onClear} type="button">
          {tr('清空选择', 'Clear Selection')}
        </button>
      </div>
    </section>
  )
}
