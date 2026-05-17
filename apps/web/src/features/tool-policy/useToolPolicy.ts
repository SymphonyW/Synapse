import { useCallback, useEffect, useMemo, useState } from 'react'
import { getToolPolicy, listTools, reloadToolPolicy, saveToolPolicy } from './api'
import type { ToolDescriptor, ToolPolicy, ToolPolicyEnvelope } from './types'

type Translate = (zh: string, en: string) => string
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

function toggleSetValue(items: string[], value: string, enabled: boolean): string[] {
  const next = new Set(items)
  if (enabled) {
    next.add(value)
  } else {
    next.delete(value)
  }
  return Array.from(next).sort()
}

export function hasRoleWildcard(policy: ToolPolicy, role: string): boolean {
  return (policy.role_allow[role] ?? []).includes('*')
}

export function hasToolForRole(policy: ToolPolicy, role: string, toolName: string): boolean {
  const tools = policy.role_allow[role] ?? []
  return tools.includes('*') || tools.includes(toolName)
}

export function useToolPolicy(tr: Translate) {
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

  return {
    envelope,
    draft,
    setDraft,
    loading,
    saving,
    reloading,
    error,
    notice,
    providerFilter,
    setProviderFilter,
    riskFilter,
    setRiskFilter,
    disabledFilter,
    setDisabledFilter,
    providers,
    filteredTools,
    dirty,
    updateRoleWildcard,
    toggleRoleTool,
    toggleDisabled,
    toggleApproval,
    handleSave,
    handleCancel,
    handleReload,
  }
}
