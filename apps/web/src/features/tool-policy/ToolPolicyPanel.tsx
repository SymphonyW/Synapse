import { useCallback } from 'react'
import { hasRoleWildcard, hasToolForRole, useToolPolicy } from './useToolPolicy'

type ToolPolicyPanelProps = {
  language: 'zh' | 'en'
}

function formatDateTime(value?: string): string {
  if (!value) {
    return '-'
  }
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleString()
}

export function ToolPolicyPanel({ language }: ToolPolicyPanelProps) {
  const tr = useCallback((zh: string, en: string): string => (language === 'zh' ? zh : en), [language])
  const policy = useToolPolicy(tr)

  return (
    <main className="tool-policy-layout">
      <section className="panel tool-policy-summary">
        <div className="tool-policy-heading">
          <div>
            <h2>{tr('工具策略', 'Tool Policy')}</h2>
            <p>
              {tr(
                '审批与禁用是两层治理；关闭“需审批”并不代表角色限制失效。',
                'Approval and role authorization are separate layers; disabling approval never bypasses role limits.',
              )}
            </p>
          </div>
          <div className="tool-policy-actions">
            <button className="ghost" disabled={!policy.dirty || policy.saving} onClick={policy.handleCancel} type="button">
              {tr('取消修改', 'Discard')}
            </button>
            <button disabled={!policy.dirty || policy.saving} onClick={() => void policy.handleSave()} type="button">
              {policy.saving ? tr('保存中...', 'Saving...') : tr('保存策略', 'Save Policy')}
            </button>
          </div>
        </div>

        {policy.error && <p className="error-banner">{policy.error}</p>}
        {policy.notice && <p className="auth-notice">{policy.notice}</p>}

        <div className="tool-policy-meta">
          <div>
            <span>{tr('来源', 'source')}</span>
            <strong>{policy.envelope?.source ?? '-'}</strong>
          </div>
          <div>
            <span>{tr('版本', 'version')}</span>
            <strong>{policy.draft.version}</strong>
          </div>
          <div>
            <span>{tr('最近更新', 'updated')}</span>
            <strong>{formatDateTime(policy.draft.updated_at)}</strong>
          </div>
          <div>
            <span>{tr('更新人', 'updated by')}</span>
            <strong>{policy.draft.updated_by || '-'}</strong>
          </div>
        </div>

        <div className="tool-policy-role-grid">
          {(['user', 'admin'] as const).map((role) => (
            <section key={role}>
              <div>
                <strong>{role}</strong>
                <label>
                  <input
                    checked={hasRoleWildcard(policy.draft, role)}
                    onChange={(event) => policy.updateRoleWildcard(role, event.target.checked)}
                    type="checkbox"
                  />
                  {tr('使用 *（当前及未来工具全放行）', 'Use * (allow current and future tools)')}
                </label>
              </div>
              <p>
                {hasRoleWildcard(policy.draft, role)
                  ? tr('当前角色由通配符授权。', 'This role is authorized by wildcard.')
                  : tr('当前角色按工具逐项授权。', 'This role is authorized tool by tool.')}
              </p>
            </section>
          ))}
        </div>

        <label className="tool-policy-description">
          {tr('说明', 'Description')}
          <textarea
            onChange={(event) =>
              policy.setDraft((previous) => ({
                ...previous,
                description: event.target.value,
              }))
            }
            placeholder={tr('例如：收紧高风险联网工具。', 'Example: tighten high-risk network tools.')}
            rows={3}
            value={policy.draft.description ?? ''}
          />
        </label>
      </section>

      <section className="panel tool-policy-catalog">
        <div className="tool-policy-toolbar">
          <div>
            <h2>{tr('工具目录', 'Tool Catalog')}</h2>
            <p>
              {tr(
                '新 provider 工具会在这里出现，并显示当前默认策略结果。',
                'New provider tools appear here with their current effective policy result.',
              )}
            </p>
          </div>
          <button className="ghost" disabled={policy.reloading} onClick={() => void policy.handleReload()} type="button">
            {policy.reloading ? tr('下发中...', 'Reloading...') : tr('重新下发', 'Reload')}
          </button>
        </div>

        <div className="tool-policy-filters">
          <label>
            provider
            <select onChange={(event) => policy.setProviderFilter(event.target.value)} value={policy.providerFilter}>
              <option value="all">{tr('全部', 'all')}</option>
              {policy.providers.map((provider) => (
                <option key={provider} value={provider}>
                  {provider}
                </option>
              ))}
            </select>
          </label>
          <label>
            risk
            <select onChange={(event) => policy.setRiskFilter(event.target.value)} value={policy.riskFilter}>
              <option value="all">{tr('全部', 'all')}</option>
              {['low', 'medium', 'high', 'critical'].map((risk) => (
                <option key={risk} value={risk}>
                  {risk}
                </option>
              ))}
            </select>
          </label>
          <label>
            {tr('禁用状态', 'disabled')}
            <select
              onChange={(event) => policy.setDisabledFilter(event.target.value as 'all' | 'enabled' | 'disabled')}
              value={policy.disabledFilter}
            >
              <option value="all">{tr('全部', 'all')}</option>
              <option value="enabled">{tr('启用', 'enabled')}</option>
              <option value="disabled">{tr('已禁用', 'disabled')}</option>
            </select>
          </label>
        </div>

        <div className="tool-policy-table" aria-live="polite">
          {policy.loading && policy.filteredTools.length === 0 && <p className="empty">{tr('加载中...', 'Loading...')}</p>}
          {!policy.loading && policy.filteredTools.length === 0 && (
            <p className="empty">{tr('没有匹配的工具。', 'No matching tools.')}</p>
          )}
          {policy.filteredTools.map((tool) => {
            const disabled = policy.draft.disabled_tools.includes(tool.name)
            const approval = policy.draft.approval_required.includes(tool.name)
            return (
              <article className={`tool-policy-row ${tool.risk_level}`} key={tool.name}>
                <div className="tool-policy-main">
                  <div className="tool-policy-title">
                    <strong>{tool.name}</strong>
                    <span>{tool.provider_name}</span>
                    <em>{tool.risk_level}</em>
                  </div>
                  <p>{tool.description}</p>
                  <small>
                    {tr('当前生效角色', 'effective roles')}: {tool.allowed_roles.join(', ') || '-'}
                  </small>
                </div>

                <div className="tool-policy-switches">
                  <label className={disabled ? 'state-disabled' : ''}>
                    <input
                      checked={disabled}
                      onChange={(event) => policy.toggleDisabled(tool.name, event.target.checked)}
                      type="checkbox"
                    />
                    {tr('禁用', 'disabled')}
                  </label>
                  <label className={approval ? 'state-approval' : ''}>
                    <input
                      checked={approval}
                      disabled={disabled}
                      onChange={(event) => policy.toggleApproval(tool.name, event.target.checked)}
                      type="checkbox"
                    />
                    {tr('需审批', 'approval')}
                  </label>
                </div>

                <div className="tool-policy-role-switches">
                  {(['user', 'admin'] as const).map((role) => (
                    <label key={`${tool.name}-${role}`}>
                      <input
                        checked={hasToolForRole(policy.draft, role, tool.name)}
                        disabled={hasRoleWildcard(policy.draft, role)}
                        onChange={(event) => policy.toggleRoleTool(role, tool.name, event.target.checked)}
                        type="checkbox"
                      />
                      {role}
                    </label>
                  ))}
                </div>
              </article>
            )
          })}
        </div>
      </section>
    </main>
  )
}
