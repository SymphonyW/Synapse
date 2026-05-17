import { useCallback, useEffect, useState } from 'react'
import { DEAD_LETTER_LIMIT } from '../../shared/utils/constants'
import type { DeadLetterTask } from '../../shared/types/domain'
import { listDeadLetters } from './api'

type Translate = (zh: string, en: string) => string

type UseDeadLettersOptions = {
  enabled: boolean
  tr: Translate
}

export function useDeadLetters({ enabled, tr }: UseDeadLettersOptions) {
  const [deadLetters, setDeadLetters] = useState<DeadLetterTask[]>([])
  const [refreshingDeadLetters, setRefreshingDeadLetters] = useState(false)
  const [error, setError] = useState('')
  const [lastUpdatedAt, setLastUpdatedAt] = useState<number | null>(null)

  const refreshDeadLetters = useCallback(async () => {
    if (!enabled) {
      setDeadLetters([])
      return
    }

    setRefreshingDeadLetters(true)
    try {
      const response = await listDeadLetters(DEAD_LETTER_LIMIT)
      setDeadLetters(response.items)
      setError('')
      setLastUpdatedAt(Date.now())
    } catch (refreshError) {
      setError(
        refreshError instanceof Error
          ? refreshError.message
          : tr('获取死信列表失败', 'Failed to fetch dead letters'),
      )
    } finally {
      setRefreshingDeadLetters(false)
    }
  }, [enabled, tr])

  useEffect(() => {
    if (!enabled) {
      setDeadLetters([])
      return
    }

    void refreshDeadLetters()
    const timer = window.setInterval(() => {
      void refreshDeadLetters()
    }, 5000)
    return () => {
      window.clearInterval(timer)
    }
  }, [enabled, refreshDeadLetters])

  return {
    deadLetters,
    refreshingDeadLetters,
    error,
    lastUpdatedAt,
    refreshDeadLetters,
    clear: () => {
      setDeadLetters([])
      setError('')
      setLastUpdatedAt(null)
    },
  }
}
