import { useCallback, useEffect, useState } from 'react'
import { AUTH_SESSION_STORAGE_KEY } from '../../shared/utils/constants'
import { normalizeUsername } from '../../shared/utils/format'
import type { AuthMode, SessionIdentity } from '../../shared/types/domain'
import { getCurrentUser, login as loginRequest, logout as logoutRequest, register as registerRequest } from './api'

type Translate = (zh: string, en: string) => string

type RegisterInput = {
  username: string
  password: string
  confirmPassword: string
}

type LoginInput = {
  username: string
  password: string
}

function loadSessionFromStorage(): SessionIdentity | null {
  if (typeof window === 'undefined') {
    return null
  }

  try {
    const raw = window.localStorage.getItem(AUTH_SESSION_STORAGE_KEY)
    if (!raw) {
      return null
    }

    const parsed = JSON.parse(raw) as Partial<SessionIdentity>
    if (typeof parsed.username !== 'string') {
      return null
    }

    if (parsed.role !== 'admin' && parsed.role !== 'user') {
      return null
    }

    const normalized = normalizeUsername(parsed.username)
    if (normalized === '') {
      return null
    }

    return {
      username: normalized,
      role: parsed.role,
    }
  } catch {
    return null
  }
}

export function useAuthSession(tr: Translate) {
  const [currentUser, setCurrentUser] = useState<SessionIdentity | null>(() =>
    loadSessionFromStorage(),
  )
  const [initializing, setInitializing] = useState(true)
  const [mode, setMode] = useState<AuthMode>('login')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    if (typeof window === 'undefined') {
      return
    }

    if (!currentUser) {
      window.localStorage.removeItem(AUTH_SESSION_STORAGE_KEY)
      return
    }

    window.localStorage.setItem(AUTH_SESSION_STORAGE_KEY, JSON.stringify(currentUser))
  }, [currentUser])

  useEffect(() => {
    let canceled = false

    const bootstrap = async () => {
      try {
        const payload = await getCurrentUser()
        if (!canceled) {
          setCurrentUser(payload.user)
        }
      } catch {
        if (!canceled) {
          setCurrentUser(null)
        }
      } finally {
        if (!canceled) {
          setInitializing(false)
        }
      }
    }

    void bootstrap()
    return () => {
      canceled = true
    }
  }, [])

  const changeMode = useCallback((nextMode: AuthMode) => {
    setMode(nextMode)
    setError('')
    setNotice('')
  }, [])

  const login = useCallback(
    async ({ username, password }: LoginInput) => {
      const normalizedName = normalizeUsername(username)
      if (normalizedName === '' || password === '') {
        setError(tr('用户名和密码不能为空。', 'Username and password are required.'))
        setNotice('')
        return null
      }

      try {
        const payload = await loginRequest(normalizedName, password)
        setCurrentUser(payload.user)
        setError('')
        setNotice('')
        return payload.user
      } catch (loginError) {
        setError(
          loginError instanceof Error
            ? loginError.message
            : tr('登录失败，请稍后重试。', 'Sign-in failed, please try again.'),
        )
        setNotice('')
        return null
      }
    },
    [tr],
  )

  const register = useCallback(
    async ({ username, password, confirmPassword }: RegisterInput) => {
      const normalizedName = normalizeUsername(username)
      if (normalizedName === '') {
        setError(tr('用户名不能为空。', 'Username is required.'))
        setNotice('')
        return false
      }

      if (normalizedName.length < 3) {
        setError(tr('用户名至少需要 3 个字符。', 'Username must be at least 3 characters.'))
        setNotice('')
        return false
      }

      if (password.length < 6) {
        setError(tr('密码至少需要 6 位。', 'Password must be at least 6 characters.'))
        setNotice('')
        return false
      }

      if (password !== confirmPassword) {
        setError(tr('两次输入的密码不一致。', 'Passwords do not match.'))
        setNotice('')
        return false
      }

      try {
        await registerRequest(normalizedName, password)
        setMode('login')
        setError('')
        setNotice(
          tr(
            '注册成功，请使用新账号登录。',
            'Registration successful. Please sign in with your new account.',
          ),
        )
        return true
      } catch (registerError) {
        setError(
          registerError instanceof Error
            ? registerError.message
            : tr('注册失败，请稍后重试。', 'Registration failed, please try again.'),
        )
        setNotice('')
        return false
      }
    },
    [tr],
  )

  const logout = useCallback(async () => {
    try {
      await logoutRequest()
    } catch {
      // 前端状态仍需回收。
    }

    setCurrentUser(null)
    setMode('login')
    setError('')
    setNotice(tr('已退出登录。', 'Signed out successfully.'))
  }, [tr])

  const clearMessages = useCallback(() => {
    setError('')
    setNotice('')
  }, [])

  return {
    currentUser,
    initializing,
    mode,
    error,
    notice,
    changeMode,
    login,
    register,
    logout,
    clearMessages,
  }
}
