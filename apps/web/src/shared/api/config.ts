const trimTrailingSlash = (value: string): string => value.replace(/\/+$/, '')

export const API_BASE_PATH = trimTrailingSlash(import.meta.env.VITE_API_BASE_PATH?.trim() ?? '')
export const HEALTH_PATH = import.meta.env.VITE_HEALTH_PATH?.trim() || '/healthz'

export function resolveApiPath(path: string): string {
  if (/^https?:\/\//.test(path)) {
    return path
  }

  if (API_BASE_PATH === '') {
    return path
  }

  return `${API_BASE_PATH}${path.startsWith('/') ? path : `/${path}`}`
}
