import { apiRequest } from '../../shared/api/client'
import type { DeadLetterResponse } from '../../shared/types/domain'

export function listDeadLetters(limit: number): Promise<DeadLetterResponse> {
  return apiRequest<DeadLetterResponse>(`/v1/dead-letters?limit=${limit}`)
}
