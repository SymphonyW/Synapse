import { apiRequest } from '../../shared/api/client'
import type { AuthPayload } from '../../shared/types/domain'

export function getCurrentUser(): Promise<AuthPayload> {
  return apiRequest<AuthPayload>('/v1/auth/me')
}

export function login(username: string, password: string): Promise<AuthPayload> {
  return apiRequest<AuthPayload>('/v1/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
}

export function register(username: string, password: string): Promise<AuthPayload> {
  return apiRequest<AuthPayload>('/v1/auth/register', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
}

export function logout(): Promise<{ status: string }> {
  return apiRequest<{ status: string }>('/v1/auth/logout', {
    method: 'POST',
  })
}
