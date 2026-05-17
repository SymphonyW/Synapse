import { useState } from 'react'
import type { FormEvent } from 'react'
import type { AuthMode } from '../../shared/types/domain'

type Translate = (zh: string, en: string) => string

type LoginInput = {
  username: string
  password: string
}

type RegisterInput = LoginInput & {
  confirmPassword: string
}

type AuthScreenProps = {
  initializing: boolean
  mode: AuthMode
  error: string
  notice: string
  onChangeMode: (mode: AuthMode) => void
  onLogin: (input: LoginInput) => void | Promise<unknown>
  onRegister: (input: RegisterInput) => void | Promise<unknown>
  tr: Translate
}

export function AuthScreen({
  initializing,
  mode,
  error,
  notice,
  onChangeMode,
  onLogin,
  onRegister,
  tr,
}: AuthScreenProps) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (mode === 'login') {
      void onLogin({ username, password })
      return
    }

    void onRegister({ username, password, confirmPassword })
  }

  const handleChangeMode = (nextMode: AuthMode) => {
    onChangeMode(nextMode)
    setPassword('')
    setConfirmPassword('')
  }

  if (initializing) {
    return (
      <div className="auth-shell">
        <section className="auth-panel">
          <p className="eyebrow">{tr('正在校验登录状态', 'Checking Session')}</p>
          <h1>{tr('请稍候...', 'Please wait...')}</h1>
          <p className="empty">
            {tr(
              '系统正在与网关同步身份信息。',
              'Synchronizing your authentication state with the gateway.',
            )}
          </p>
        </section>
      </div>
    )
  }

  return (
    <div className="auth-shell">
      <section className="auth-hero">
        <p className="eyebrow">{tr('Synapse 安全入口', 'Synapse Secure Access')}</p>
        <h1>{tr('登录后进入控制台', 'Sign In To Continue')}</h1>
        <p>
          {tr(
            '注册普通用户后可使用聊天端，管理员可进入运维台。',
            'Register as a regular user for chat access, and sign in as admin for the ops console.',
          )}
        </p>
      </section>

      <section className="auth-panel">
        <div className="auth-tabs" role="tablist" aria-label={tr('身份操作', 'Authentication actions')}>
          <button
            className={mode === 'login' ? 'auth-tab active' : 'auth-tab'}
            onClick={() => handleChangeMode('login')}
            type="button"
          >
            {tr('登录', 'Sign In')}
          </button>
          <button
            className={mode === 'register' ? 'auth-tab active' : 'auth-tab'}
            onClick={() => handleChangeMode('register')}
            type="button"
          >
            {tr('注册', 'Register')}
          </button>
        </div>

        {error && <p className="error-banner">{error}</p>}
        {notice && <p className="auth-notice">{notice}</p>}

        <form className="auth-form" onSubmit={handleSubmit}>
          <label>
            {tr('用户名', 'Username')}
            <input
              autoComplete="username"
              onChange={(event) => setUsername(event.target.value)}
              placeholder={tr('输入用户名', 'Enter username')}
              value={username}
            />
          </label>

          <label>
            {tr('密码', 'Password')}
            <input
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={tr('至少 6 位', 'At least 6 characters')}
              type="password"
              value={password}
            />
          </label>

          {mode === 'register' && (
            <label>
              {tr('确认密码', 'Confirm Password')}
              <input
                autoComplete="new-password"
                onChange={(event) => setConfirmPassword(event.target.value)}
                placeholder={tr('再次输入密码', 'Repeat your password')}
                type="password"
                value={confirmPassword}
              />
            </label>
          )}

          <button type="submit">
            {mode === 'login' ? tr('进入系统', 'Enter Console') : tr('创建账号', 'Create Account')}
          </button>
        </form>

        <p className="auth-footnote">
          {tr(
            '运维台仅管理员可访问，管理员账号由系统预置维护。',
            'Ops console is admin-only, and the administrator account is managed by the system.',
          )}
        </p>
      </section>
    </div>
  )
}
