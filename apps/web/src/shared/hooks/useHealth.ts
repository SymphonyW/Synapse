import { useCallback, useEffect, useState } from 'react'
import { apiRequest } from '../api/client'
import { HEALTH_PATH } from '../api/config'
import type { HealthResponse } from '../types/domain'

type Translate = (zh: string, en: string) => string

export function useHealth(enabled: boolean, tr: Translate) {
  const [health, setHealth] = useState<HealthResponse | null>(null)

  const refreshHealth = useCallback(async () => {
    try {
      const payload = await apiRequest<HealthResponse>(HEALTH_PATH)
      setHealth(payload)
    } catch (error) {
      setHealth({
        status: 'degraded',
        error: error instanceof Error ? error.message : tr('网关不可达', 'Gateway unreachable'),
      })
    }
  }, [tr])

  useEffect(() => {
    if (!enabled) {
      return
    }

    const initialTimer = window.setTimeout(() => {
      void refreshHealth()
    }, 0)
    const timer = window.setInterval(() => {
      void refreshHealth()
    }, 10000)

    return () => {
      window.clearTimeout(initialTimer)
      window.clearInterval(timer)
    }
  }, [enabled, refreshHealth])

  return { health, refreshHealth }
}
