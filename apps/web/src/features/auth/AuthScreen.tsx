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
        <div className="auth-brand-lockup">
          <img className="auth-logo" src="/icon.png" alt="Synapse" />
          <div>
            <p className="eyebrow">Synapse</p>
            <span>{tr('Agent 运行控制面', 'Agent Control Plane')}</span>
          </div>
        </div>

        <div className="auth-hero-copy">
          <span className="auth-kicker">{tr('任务、工具、审批与追踪', 'Tasks, tools, approvals, and traces')}</span>
          <h1>{tr('把 Agent 执行过程纳入控制', 'Bring agent execution under control')}</h1>
          <p>
            {tr(
              '从任务入队到工具审批，再到事件追踪和重放，所有关键状态都在同一个工作台里闭环。',
              'From queueing and tool approvals to event traces and replay, every critical state stays inside one console.',
            )}
          </p>
        </div>

        <div className="auth-flow" aria-label={tr('运行链路', 'Execution flow')}>
          <span>{tr('入队', 'Queue')}</span>
          <i aria-hidden="true" />
          <span>{tr('治理', 'Policy')}</span>
          <i aria-hidden="true" />
          <span>{tr('追踪', 'Trace')}</span>
          <i aria-hidden="true" />
          <span>{tr('恢复', 'Replay')}</span>
        </div>

        <div className="auth-capabilities" aria-label={tr('核心能力', 'Core capabilities')}>
          <div>
            <strong>{tr('可暂停', 'Pause')}</strong>
            <span>{tr('高风险工具先审批', 'Approve risky tools first')}</span>
          </div>
          <div>
            <strong>{tr('可观测', 'Observe')}</strong>
            <span>{tr('SSE 事件持续落库', 'Persisted SSE event stream')}</span>
          </div>
          <div>
            <strong>{tr('可恢复', 'Recover')}</strong>
            <span>{tr('失败任务可重放', 'Replay failed tasks')}</span>
          </div>
        </div>
      </section>

      <section className="auth-panel">
        <div className="auth-panel-head">
          <span>{tr('安全入口', 'Secure Access')}</span>
          <h2>{mode === 'login' ? tr('登录控制台', 'Sign in to console') : tr('创建账号', 'Create account')}</h2>
          <p>
            {mode === 'login'
              ? tr('使用账号进入 Synapse 工作台。', 'Use your account to enter the Synapse workspace.')
              : tr('注册后可进入用户端发起任务。', 'Register to start tasks from the client workspace.')}
          </p>
        </div>

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
