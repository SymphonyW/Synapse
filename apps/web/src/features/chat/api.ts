import { apiRequest } from '../../shared/api/client'
import type { DeleteConversationResponse } from '../../shared/types/domain'

export function deleteConversation(conversationID: string): Promise<DeleteConversationResponse> {
  return apiRequest<DeleteConversationResponse>(
    `/v1/conversations/${encodeURIComponent(conversationID)}`,
    { method: 'DELETE' },
  )
}
