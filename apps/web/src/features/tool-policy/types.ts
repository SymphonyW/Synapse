export type ToolPolicy = {
  role_allow: Record<string, string[]>
  approval_required: string[]
  disabled_tools: string[]
  version: number
  updated_at?: string
  updated_by?: string
  description?: string
}

export type ToolPolicyEnvelope = {
  source: 'managed' | 'runtime_default' | string
  applied: boolean
  policy: ToolPolicy
}

export type ToolDescriptor = {
  name: string
  description: string
  risk_level: 'low' | 'medium' | 'high' | 'critical' | string
  requires_approval: boolean
  provider_name: string
  currently_disabled: boolean
  allowed_roles: string[]
}

export type ToolListResponse = {
  items: ToolDescriptor[]
  count: number
}
