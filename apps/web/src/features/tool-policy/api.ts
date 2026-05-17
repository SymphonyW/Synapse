import type { ToolListResponse, ToolPolicy, ToolPolicyEnvelope } from './types'
import { apiRequest } from '../../shared/api/client'

export function getToolPolicy(): Promise<ToolPolicyEnvelope> {
  return apiRequest<ToolPolicyEnvelope>('/v1/admin/tool-policy')
}

export function listTools(): Promise<ToolListResponse> {
  return apiRequest<ToolListResponse>('/v1/admin/tools')
}

export function saveToolPolicy(policy: Pick<ToolPolicy, 'role_allow' | 'approval_required' | 'disabled_tools' | 'description'>): Promise<ToolPolicyEnvelope> {
  return apiRequest<ToolPolicyEnvelope>('/v1/admin/tool-policy', {
    method: 'PUT',
    body: JSON.stringify(policy),
  })
}

export function reloadToolPolicy(): Promise<ToolPolicyEnvelope> {
  return apiRequest<ToolPolicyEnvelope>('/v1/admin/tool-policy/reload', {
    method: 'POST',
  })
}
