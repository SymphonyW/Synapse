import { useCallback, useEffect, useMemo, useState } from 'react'
import { getToolPolicy, listTools, reloadToolPolicy, saveToolPolicy } from './api'
import type { ToolDescriptor, ToolPolicy, ToolPolicyEnvelope } from './types'

type ToolPolicyPanelProps = {
  language: 'zh' | 'en'
}

type DisabledFilter = 'all' | 'enabled' | 'disabled'

const EMPTY_POLICY: ToolPolicy = {
  role_allow: { user: [], admin: ['*'] },
  approval_required: [],
  disabled_tools: [],
  version: 0,
}

function clonePolicy(policy: ToolPolicy): ToolPolicy {
  return {
    ...policy,
    role_allow: Object.fromEntries(
      Object.entries(policy.role_allow ?? {}).map(([role, tools]) => [role, [...tools]]),
    ),
    approval_required: [...(policy.approval_required ?? [])],
    disabled_tools: [...(policy.disabled_tools ?? [])],
  }
}

function hasRoleWildcard(policy: ToolPolicy, role: string): boolean {
  return (policy.role_allow[role] ?? []).includes('*')
}

function hasToolForRole(policy: ToolPolicy, role: string, toolName: string): boolean {
  const tools = policy.role_allow[role] ?? []
  return tools.includes('*') || tools.includes(toolName)
}

function toggleSetValue(items: string[], value: string, enabled: boolean): string[] {
  const next = new Set(items)
  if (enabled) {
    next.add(value)
  } else {
    next.delete(value)
  }
  return Array.from(next).sort()
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
  const [envelope, setEnvelope] = useState<ToolPolicyEnvelope | null>(null)
  const [tools, setTools] = useState<ToolDescriptor[]>([])
  const [draft, setDraft] = useState<ToolPolicy>(EMPTY_POLICY)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [reloading, setReloading] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [providerFilter, setProviderFilter] = useState('all')
  const [riskFilter, setRiskFilter] = useState('all')
  const [disabledFilter, setDisabledFilter] = useState<DisabledFilter>('all')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [policyPayload, toolsPayload] = await Promise.all([getToolPolicy(), listTools()])
      setEnvelope(policyPayload)
      setDraft(clonePolicy(policyPayload.policy))
      setTools(toolsPayload.items)
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : tr('加载失败', 'Failed to load'))
    } finally {
      setLoading(false)
    }
  }, [tr])

  useEffect(() => {
    void load()
  }, [load])

  const providers = useMemo(
    () => Array.from(new Set(tools.map((tool) => tool.provider_name).filter(Boolean))).sort(),
    [tools],
  )

  const filteredTools = useMemo(
    () =>
      tools.filter((tool) => {
        if (providerFilter !== 'all' && tool.provider_name !== providerFilter) {
          return false
        }
        if (riskFilter !== 'all' && tool.risk_level !== riskFilter) {
          return false
        }
        if (disabledFilter === 'enabled' && draft.disabled_tools.includes(tool.name)) {
          return false
        }
        if (disabledFilter === 'disabled' && !draft.disabled_tools.includes(tool.name)) {
          return false
        }
        return true
      }),
    [disabledFilter, draft.disabled_tools, providerFilter, riskFilter, tools],
  )

  const dirty = useMemo(() => {
    if (!envelope) {
      return false
    }
    return JSON.stringify(draft) !== JSON.stringify(envelope.policy)
  }, [draft, envelope])

  const updateRoleWildcard = (role: string, enabled: boolean) => {
    setDraft((previous) => ({
      ...previous,
      role_allow: {
        ...previous.role_allow,
        [role]: enabled
          ? ['*']
          : (previous.role_allow[role] ?? []).filter((item) => item !== '*'),
      },
    }))
  }

  const toggleRoleTool = (role: string, toolName: string, enabled: boolean) => {
    setDraft((previous) => ({
      ...previous,
      role_allow: {
        ...previous.role_allow,
        [role]: toggleSetValue(
          (previous.role_allow[role] ?? []).filter((item) => item !== '*'),
          toolName,
          enabled,
        ),
      },
    }))
  }

  const toggleDisabled = (toolName: string, enabled: boolean) => {
    setDraft((previous) => ({
      ...previous,
      disabled_tools: toggleSetValue(previous.disabled_tools, toolName, enabled),
    }))
  }

  const toggleApproval = (toolName: string, enabled: boolean) => {
    setDraft((previous) => ({
      ...previous,
      approval_required: toggleSetValue(previous.approval_required, toolName, enabled),
    }))
  }

  const handleSave = async () => {
    setSaving(true)
    setError('')
    setNotice('')
    try {
      const payload = await saveToolPolicy({
        role_allow: draft.role_allow,
        approval_required: draft.approval_required,
        disabled_tools: draft.disabled_tools,
        description: draft.description,
      })
      setEnvelope(payload)
      setDraft(clonePolicy(payload.policy))
      await load()
      setNotice(tr('策略已保存并热更新。', 'Policy saved and hot-applied.'))
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : tr('保存失败', 'Save failed'))
    } finally {
      setSaving(false)
    }
  }

  const handleCancel = () => {
    if (!envelope) {
      return
    }
    setDraft(clonePolicy(envelope.policy))
    setNotice('')
    setError('')
  }

  const handleReload = async () => {
    setReloading(true)
    setError('')
    setNotice('')
    try {
      const payload = await reloadToolPolicy()
      setEnvelope(payload)
      setDraft(clonePolicy(payload.policy))
      await load()
      setNotice(tr('策略已重新下发。', 'Policy reloaded into runtime.'))
    } catch (reloadError) {
      setError(reloadError instanceof Error ? reloadError.message : tr('重新加载失败', 'Reload failed'))
    } finally {
      setReloading(false)
    }
  }

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
            <button className="ghost" disabled={!dirty || saving} onClick={handleCancel} type="button">
              {tr('取消修改', 'Discard')}
            </button>
            <button disabled={!dirty || saving} onClick={() => void handleSave()} type="button">
              {saving ? tr('保存中...', 'Saving...') : tr('保存策略', 'Save Policy')}
            </button>
          </div>
        </div>

        {error && <p className="error-banner">{error}</p>}
        {notice && <p className="auth-notice">{notice}</p>}

        <div className="tool-policy-meta">
          <div>
            <span>{tr('来源', 'source')}</span>
            <strong>{envelope?.source ?? '-'}</strong>
          </div>
          <div>
            <span>{tr('版本', 'version')}</span>
            <strong>{draft.version}</strong>
          </div>
          <div>
            <span>{tr('最近更新', 'updated')}</span>
            <strong>{formatDateTime(draft.updated_at)}</strong>
          </div>
          <div>
            <span>{tr('更新人', 'updated by')}</span>
            <strong>{draft.updated_by || '-'}</strong>
          </div>
        </div>

        <div className="tool-policy-role-grid">
          {(['user', 'admin'] as const).map((role) => (
            <section key={role}>
              <div>
                <strong>{role}</strong>
                <label>
                  <input
                    checked={hasRoleWildcard(draft, role)}
                    onChange={(event) => updateRoleWildcard(role, event.target.checked)}
                    type="checkbox"
                  />
                  {tr('使用 *（当前及未来工具全放行）', 'Use * (allow current and future tools)')}
                </label>
              </div>
              <p>
                {hasRoleWildcard(draft, role)
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
              setDraft((previous) => ({
                ...previous,
                description: event.target.value,
              }))
            }
            placeholder={tr('例如：收紧高风险联网工具。', 'Example: tighten high-risk network tools.')}
            rows={3}
            value={draft.description ?? ''}
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
          <button className="ghost" disabled={reloading} onClick={() => void handleReload()} type="button">
            {reloading ? tr('下发中...', 'Reloading...') : tr('重新下发', 'Reload')}
          </button>
        </div>

        <div className="tool-policy-filters">
          <label>
            provider
            <select onChange={(event) => setProviderFilter(event.target.value)} value={providerFilter}>
              <option value="all">{tr('全部', 'all')}</option>
              {providers.map((provider) => (
                <option key={provider} value={provider}>
                  {provider}
                </option>
              ))}
            </select>
          </label>
          <label>
            risk
            <select onChange={(event) => setRiskFilter(event.target.value)} value={riskFilter}>
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
              onChange={(event) => setDisabledFilter(event.target.value as DisabledFilter)}
              value={disabledFilter}
            >
              <option value="all">{tr('全部', 'all')}</option>
              <option value="enabled">{tr('启用', 'enabled')}</option>
              <option value="disabled">{tr('已禁用', 'disabled')}</option>
            </select>
          </label>
        </div>

        <div className="tool-policy-table" aria-live="polite">
          {loading && tools.length === 0 && <p className="empty">{tr('加载中...', 'Loading...')}</p>}
          {!loading && filteredTools.length === 0 && (
            <p className="empty">{tr('没有匹配的工具。', 'No matching tools.')}</p>
          )}
          {filteredTools.map((tool) => {
            const disabled = draft.disabled_tools.includes(tool.name)
            const approval = draft.approval_required.includes(tool.name)
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
                      onChange={(event) => toggleDisabled(tool.name, event.target.checked)}
                      type="checkbox"
                    />
                    {tr('禁用', 'disabled')}
                  </label>
                  <label className={approval ? 'state-approval' : ''}>
                    <input
                      checked={approval}
                      disabled={disabled}
                      onChange={(event) => toggleApproval(tool.name, event.target.checked)}
                      type="checkbox"
                    />
                    {tr('需审批', 'approval')}
                  </label>
                </div>

                <div className="tool-policy-role-switches">
                  {(['user', 'admin'] as const).map((role) => (
                    <label key={`${tool.name}-${role}`}>
                      <input
                        checked={hasToolForRole(draft, role, tool.name)}
                        disabled={hasRoleWildcard(draft, role)}
                        onChange={(event) => toggleRoleTool(role, tool.name, event.target.checked)}
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
