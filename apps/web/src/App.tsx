import { useEffect, useMemo, useState } from 'react'
import { AuthScreen } from './features/auth/AuthScreen'
import { useAuthSession } from './features/auth/useAuthSession'
import { ClientChatPanel } from './features/chat/ClientChatPanel'
import { MemoryPanel } from './features/memory/MemoryPanel'
import { OpsPanel } from './features/ops/OpsPanel'
import { ToolPolicyPanel } from './features/tool-policy/ToolPolicyPanel'
import { AppHeader } from './shared/components/AppHeader'
import { useHealth } from './shared/hooks/useHealth'
import type { Language, ViewMode } from './shared/types/domain'
import {
  LANGUAGE_STORAGE_KEY,
  VIEW_MODE_STORAGE_KEY,
} from './shared/utils/constants'
import './App.css'

function App() {
  const [language, setLanguage] = useState<Language>(() => {
    if (typeof window === 'undefined') {
      return 'zh'
    }
    return window.localStorage.getItem(LANGUAGE_STORAGE_KEY) === 'en' ? 'en' : 'zh'
  })
  const tr = useMemo(
    () => (zh: string, en: string): string => (language === 'zh' ? zh : en),
    [language],
  )
  const [viewMode, setViewMode] = useState<ViewMode>(() => {
    if (typeof window === 'undefined') {
      return 'client'
    }
    const persisted = window.localStorage.getItem(VIEW_MODE_STORAGE_KEY)
    return persisted === 'ops' || persisted === 'memory' || persisted === 'policy'
      ? persisted
      : 'client'
  })

  const auth = useAuthSession(tr)
  const { health } = useHealth(Boolean(auth.currentUser), tr)
  const isAdmin = auth.currentUser?.role === 'admin'
  const effectiveViewMode =
    !isAdmin && (viewMode === 'ops' || viewMode === 'policy') ? 'client' : viewMode

  useEffect(() => {
    window.localStorage.setItem(LANGUAGE_STORAGE_KEY, language)
  }, [language])

  useEffect(() => {
    window.localStorage.setItem(VIEW_MODE_STORAGE_KEY, viewMode)
  }, [viewMode])

  if (!auth.currentUser) {
    return (
      <AuthScreen
        error={auth.error}
        initializing={auth.initializing}
        mode={auth.mode}
        notice={auth.notice}
        onChangeMode={auth.changeMode}
        onLogin={async (input) => {
          const user = await auth.login(input)
          if (user) {
            setViewMode('client')
          }
        }}
        onRegister={auth.register}
        tr={tr}
      />
    )
  }

  const shellClass =
    effectiveViewMode === 'client'
      ? 'app-shell app-shell-client'
      : effectiveViewMode === 'memory'
        ? 'app-shell app-shell-memory'
        : effectiveViewMode === 'policy'
          ? 'app-shell app-shell-policy'
          : 'app-shell'

  return (
    <div className={shellClass}>
      <AppHeader
        currentUser={auth.currentUser}
        health={health}
        language={language}
        mode={effectiveViewMode}
        onChangeLanguage={() => setLanguage((previous) => (previous === 'zh' ? 'en' : 'zh'))}
        onLogout={auth.logout}
        onNavigate={setViewMode}
        tr={tr}
      />

      {effectiveViewMode === 'client' && (
        <ClientChatPanel currentUser={auth.currentUser} language={language} tr={tr} />
      )}
      {effectiveViewMode === 'memory' && <MemoryPanel currentUser={auth.currentUser} language={language} />}
      {effectiveViewMode === 'ops' && isAdmin && (
        <OpsPanel currentUser={auth.currentUser} language={language} tr={tr} />
      )}
      {effectiveViewMode === 'policy' && isAdmin && <ToolPolicyPanel language={language} />}
    </div>
  )
}

export default App
