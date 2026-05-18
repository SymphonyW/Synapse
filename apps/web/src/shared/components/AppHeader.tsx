import type { HealthResponse, Language, SessionIdentity, ViewMode } from '../types/domain'

type Translate = (zh: string, en: string) => string

type AppHeaderProps = {
  currentUser: SessionIdentity
  health: HealthResponse | null
  language: Language
  mode: ViewMode
  onChangeLanguage: () => void
  onLogout: () => void | Promise<void>
  onNavigate: (mode: ViewMode) => void
  tr: Translate
}

function healthStatusLabel(status: string | undefined, tr: Translate): string {
  switch (status) {
    case 'ok':
      return tr('正常', 'ok')
    case 'degraded':
      return tr('降级', 'degraded')
    default:
      return status ?? tr('未知', 'unknown')
  }
}

function healthToneClass(status: string | undefined): string {
  switch (status) {
    case 'ok':
      return 'health-pill health-pill-ok'
    case 'degraded':
      return 'health-pill health-pill-warning'
    default:
      return 'health-pill health-pill-unknown'
  }
}

export function AppHeader({
  currentUser,
  health,
  language,
  mode,
  onChangeLanguage,
  onLogout,
  onNavigate,
  tr,
}: AppHeaderProps) {
  const isAdmin = currentUser.role === 'admin'
  const titles: Record<ViewMode, { eyebrow: string; heading: string; description: string }> = {
    client: {
      eyebrow: tr('Synapse 用户端', 'Synapse Client'),
      heading: tr('任务客户端', 'Task Client'),
      description: tr('更清晰的 Agent 任务协作工作台', 'A clearer workspace for agent task collaboration.'),
    },
    memory: {
      eyebrow: tr('Synapse 记忆', 'Synapse Memory'),
      heading: tr('长期记忆管理', 'Long-term Memory'),
      description: tr('管理、回想并调用系统记忆', 'Manage, recall, and inspect long-term memory.'),
    },
    ops: {
      eyebrow: tr('Synapse 管理中心', 'Synapse Admin'),
      heading: tr('任务运维台', 'Task Operations'),
      description: tr('运营任务、异常和执行状态', 'Operate tasks, failures, and execution state.'),
    },
    policy: {
      eyebrow: tr('Synapse 管理中心', 'Synapse Admin'),
      heading: tr('工具策略', 'Tool Policy'),
      description: tr('查看并维护工具调用策略', 'Review and maintain tool access policy.'),
    },
  }

  const title = titles[mode]

  return (
    <header className="topbar">
      <div className="topbar-copy">
        <p className="eyebrow">{title.eyebrow}</p>
        <h1>{title.heading}</h1>
        <p className="topbar-description">{title.description}</p>
      </div>
      <div className="topbar-actions">
        <div className="account-pill">
          <div>
            <strong>{currentUser.username}</strong>
            <span>{isAdmin ? tr('管理员', 'admin') : tr('普通用户', 'user')}</span>
          </div>
          <button className="ghost small" onClick={() => void onLogout()} type="button">
            {tr('退出', 'Sign Out')}
          </button>
        </div>

        {mode !== 'client' && (
          <button className="mode-switch ghost" onClick={() => onNavigate('client')} type="button">
            {tr('进入用户端', 'Open Client')}
          </button>
        )}
        {mode !== 'memory' && (
          <button className="mode-switch ghost" onClick={() => onNavigate('memory')} type="button">
            {tr('记忆', 'Memory')}
          </button>
        )}
        {isAdmin ? (
          <>
            {mode !== 'ops' && (
              <button className="mode-switch ghost" onClick={() => onNavigate('ops')} type="button">
                {tr('进入运维台', 'Open Ops Console')}
              </button>
            )}
            {mode !== 'policy' && (
              <button className="mode-switch ghost" onClick={() => onNavigate('policy')} type="button">
                {tr('工具策略', 'Tool Policy')}
              </button>
            )}
          </>
        ) : (
          <button
            className="mode-switch ghost ops-locked"
            disabled
            title={tr('仅管理员可以进入运维台。', 'Ops console is available for admin only.')}
            type="button"
          >
            {tr('运维台（管理员）', 'Ops Console (Admin)')}
          </button>
        )}

        <button className="language-switch" onClick={onChangeLanguage} type="button">
          {language === 'zh' ? 'EN' : '中文'}
        </button>

        <div className="health-card">
          <p>{tr('网关健康状态', 'Gateway Health')}</p>
          <div className="health-card-row">
            <strong className={healthToneClass(health?.status)}>
              <span aria-hidden="true" />
              {healthStatusLabel(health?.status, tr)}
            </strong>
            <span className="health-provider">
              {health?.model_provider ?? health?.error ?? tr('暂无提供方信息', 'No provider data')}
            </span>
          </div>
        </div>
      </div>
    </header>
  )
}
