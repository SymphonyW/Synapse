import type { TaskStatus } from '../types/domain'

export const STREAM_EVENT_TYPES = [
  'info',
  'started',
  'token',
  'paused',
  'approval_granted',
  'resume_requested',
  'cancel_requested',
  'canceled',
  'completed',
  'failed',
  'dead_lettered',
  'replay_requested',
  'terminal',
  'unspecified',
]

export const TASK_STATUS_ORDER: TaskStatus[] = [
  'queued',
  'running',
  'paused',
  'completed',
  'failed',
  'canceled',
]

export const DEAD_LETTER_LIMIT = 100
export const TASK_LIMIT = 120
export const BATCH_RESULT_HISTORY_LIMIT = 8
export const LANGUAGE_STORAGE_KEY = 'synapse.web.language'
export const VIEW_MODE_STORAGE_KEY = 'synapse.web.view-mode'
export const AUTH_SESSION_STORAGE_KEY = 'synapse.web.auth.session'
export const CLIENT_CONVERSATION_ID_KEY = 'conversation_id'
export const CLIENT_USER_MESSAGE_KEY = 'user_message'
export const NEW_CONVERSATION_DRAFT_ID = '__draft__'
export const DEFAULT_APPROVED_TOOLS =
  'browser_fetch,http_api,open_url,extract_text,summarize_page,retrieval,calculator'
