import type { ToolListResponse, ToolPolicy, ToolPolicyEnvelope } from './types'

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  })

  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`
    try {
      const payload = (await response.json()) as { error?: string }
      if (payload.error) {
        detail = payload.error
      }
    } catch {
      // keep the transport status text
    }
    throw new Error(detail)
  }

  return (await response.json()) as T
}

export function getToolPolicy(): Promise<ToolPolicyEnvelope> {
  return requestJson<ToolPolicyEnvelope>('/v1/admin/tool-policy')
}

export function listTools(): Promise<ToolListResponse> {
  return requestJson<ToolListResponse>('/v1/admin/tools')
}

export function saveToolPolicy(policy: Pick<ToolPolicy, 'role_allow' | 'approval_required' | 'disabled_tools' | 'description'>): Promise<ToolPolicyEnvelope> {
  return requestJson<ToolPolicyEnvelope>('/v1/admin/tool-policy', {
    method: 'PUT',
    body: JSON.stringify(policy),
  })
}

export function reloadToolPolicy(): Promise<ToolPolicyEnvelope> {
  return requestJson<ToolPolicyEnvelope>('/v1/admin/tool-policy/reload', {
    method: 'POST',
  })
}
