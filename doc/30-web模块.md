# Web 模块

Web 是 Synapse 的操作入口，提供普通用户聊天视图和管理员运维视图。当前 Web 既可用 Vite 本地启动，也已被 [docker-compose.yml](../docker-compose.yml) 编排为独立服务。

## 关键文件

| 文件 | 说明 |
|---|---|
| [App.tsx](../apps/web/src/App.tsx) | 顶层布局、模式切换与页面编排 |
| [shared/api/client.ts](../apps/web/src/shared/api/client.ts) | 统一 API client、错误处理和 JSON 解析 |
| [shared/hooks/useTaskEvents.ts](../apps/web/src/shared/hooks/useTaskEvents.ts) | SSE、续传、事件缓存和历史补水 |
| [features/auth](../apps/web/src/features/auth) | 鉴权请求、会话 hook 和登录组件 |
| [features/chat](../apps/web/src/features/chat) | 用户端聊天面板 |
| [features/ops](../apps/web/src/features/ops) | 运维主面板与死信 hook |
| [features/tasks](../apps/web/src/features/tasks) | 任务 API、任务 hook、任务列表和详情 |
| [App.css](../apps/web/src/App.css) | 页面样式 |
| [features/trace](../apps/web/src/features/trace) | Agent Trace parser、工作台组件和导出逻辑 |
| [main.tsx](../apps/web/src/main.tsx) | React 入口 |
| [vite.config.ts](../apps/web/vite.config.ts) | Vite 配置和 `/v1`、`/healthz` 代理 |
| [package.json](../apps/web/package.json) | npm 脚本和依赖 |

## 技术栈

| 分类 | 技术 |
|---|---|
| 框架 | React 19 |
| 语言 | TypeScript |
| 构建 | Vite 8 |
| Markdown | react-markdown、remark-gfm、remark-breaks |
| 代码检查 | ESLint |
| 测试 | Vitest、React Testing Library |

## 启动

```powershell
Set-Location apps/web
npm install
npm run dev
```

默认地址：http://127.0.0.1:5173。

Vite 代理：

| 路径 | 目标 |
|---|---|
| `/v1` | `http://127.0.0.1:8080` |
| `/healthz` | `http://127.0.0.1:8080` |

容器化启动：

```powershell
docker compose up --build -d
```

默认访问地址仍为 `http://127.0.0.1:5173`。若要用容器内 Vite 开发模式，可执行：

```powershell
docker compose --profile web-dev up --build web-dev
```

## 视图模式

| 模式 | 说明 |
|---|---|
| `client` | 普通用户聊天入口，按会话组织任务 |
| `ops` | 管理员运维台，管理任务、审批、取消、死信和 Agent Trace 工作台 |
| `policy` | 管理员工具策略页，管理禁用、审批和角色白名单 |

localStorage：

| Key | 说明 |
|---|---|
| `synapse.web.language` | 中英文切换 |
| `synapse.web.view-mode` | 当前视图模式 |
| `synapse.web.auth.session` | 当前会话身份摘要，不保存密码 |

## 核心能力

| 能力 | 说明 |
|---|---|
| 认证 | 登录、注册、退出、自动查询当前用户 |
| 会话视图 | 按 `conversation_id` 聚合任务，展示多轮聊天 |
| 会话删除 | 调用 `DELETE /v1/conversations/{conversationID}` 删除当前会话 |
| 任务创建 | 提交 prompt 和 metadata，支持 Agent 开关 |
| SSE | 订阅选中任务事件，支持 `last_event_id` 续传和 event_id 去重 |
| Agent Trace 工作台 | 将标准化 info 事件解析成结构化阶段、step、工具调用、审批、重规划、评估和诊断摘要 |
| 审批恢复 | paused 任务可 approve 并恢复执行 |
| 取消 | 支持单任务取消和批量取消 |
| 死信 | 管理员查看死信并重放 |
| 工具策略中心 | 查看工具目录，按 provider / risk / disabled 过滤，编辑禁用、审批与角色白名单 |
| 记忆 | 提供长期记忆管理页，支持列表、召回测试、手工写入和删除 |

## 数据刷新

| 数据 | 策略 |
|---|---|
| 健康状态 | 10 秒轮询 |
| 任务列表 | 4 秒轮询 |
| 当前任务详情 | 1.5 秒轮询，作为 SSE 兜底 |
| 死信列表 | 5 秒轮询，仅运维视图 |
| SSE | 选中任务后连接，收到 terminal 后关闭 |

## 工程结构

前端现按“特性 + shared”拆分：

| 目录 | 职责 |
|---|---|
| `features/auth` | 登录/注册、会话恢复 |
| `features/chat` | 用户聊天入口、会话列表、Agent 时间线 |
| `features/ops` | 运维台、死信 |
| `features/tasks` | 任务 API、任务状态与任务视图 |
| `features/memory` | 长期记忆 |
| `features/tool-policy` | 工具策略 |
| `features/trace` | Trace 工作台 |
| `shared/api` | API base path、统一 client |
| `shared/hooks` | `useHealth`、`useTaskEvents` |
| `shared/types` / `shared/utils` / `shared/components` | 公共类型、工具函数和壳层组件 |

详见 [31-web前端工程结构](31-web前端工程结构.md)。

## Agent Trace 工作台

运维视图中的 Trace 工作台由独立 parser/model 层驱动：

| 文件 | 作用 |
|---|---|
| `traceTypes.ts` | 定义原始事件、step、工具调用、审批、重规划、评估和导出摘要模型 |
| `traceParser.ts` | 将原始事件数组转换为结构化 Trace；缺失阶段或坏 JSON 时尽量保留已知信息 |
| `TraceWorkbench.tsx` | 提供结构化视图 / 原始 JSON 切换、导出和诊断摘要 |

当前结构化视图可展示：

1. 任务基础信息、`perceive`、`memory_recall`、`plan`；
2. 每个 step 内的 `tool_selected`、`tool_started`、`tool_finished`、`tool_failed`、`approval_required`；
3. `observe`、`reflect`、`replan`、`synthesis_mode`、`memory_write`、`evaluate`；
4. 小型诊断摘要：工具调用数、成功/失败数、是否审批暂停、是否重规划、最后失败原因。

交互层面：

1. 阶段导航用于快速跳转；
2. step 可折叠；
3. approval / replan 会高亮；
4. 原始事件 JSON 可复制；
5. 当前任务 Trace 可导出为 `synapse-trace-task-{taskId}.json`。

## 调用接口

| 接口 | 用途 |
|---|---|
| `GET /healthz` | 健康状态 |
| `POST /v1/auth/register` | 注册 |
| `POST /v1/auth/login` | 登录 |
| `POST /v1/auth/logout` | 退出 |
| `GET /v1/auth/me` | 当前用户 |
| `POST /v1/tasks` | 创建任务 |
| `GET /v1/tasks` | 列任务 |
| `GET /v1/tasks/{taskID}` | 任务详情 |
| `GET /v1/tasks/{taskID}/events` | SSE |
| `POST /v1/tasks/{taskID}/cancel` | 单任务取消 |
| `POST /v1/tasks/cancel` | 批量取消 |
| `POST /v1/tasks/{taskID}/approve` | 审批恢复 |
| `POST /v1/tasks/{taskID}/replay` | 任务重放 |
| `DELETE /v1/conversations/{conversationID}` | 删除会话 |
| `GET /v1/dead-letters` | 死信列表 |
| `GET /v1/admin/tool-policy` | 当前工具策略 |
| `PUT /v1/admin/tool-policy` | 保存并热应用工具策略 |
| `POST /v1/admin/tool-policy/reload` | 重新下发已保存策略 |
| `GET /v1/admin/tools` | 当前工具目录与有效治理状态 |

## 权限行为

1. 非管理员不能进入运维视图。
2. 非管理员不能进入工具策略页。
3. 普通用户只能看到自己的任务。
4. 管理员可查看全局任务、死信和工具策略。
5. 后端是权限最终裁决方，前端只做体验层控制。

## 工具策略页

`features/tool-policy` 提供独立管理面：

1. `approval_required=false` 只表示不额外要求审批，不表示绕过角色授权；
2. `disabled_tools` 与 `approval_required` 使用不同视觉状态，避免管理员把“需审批”误认成“不可用”；
3. `role_allow` 里的 `*` 表示角色默认允许当前和未来工具，但仍受禁用与审批规则约束；
4. 对高风险工具做醒目标识；
5. 保存成功后会立即重新拉取策略和工具目录，以展示真实生效结果。

## 当前限制

| 限制 | 建议 |
|---|---|
| `App.tsx` 已显著缩小 | 继续约束页面逻辑只做编排，不再回流业务实现 |
| 当前以单元/组件测试为主 | 后续可继续补充关键链路 E2E |
| 已完成基础容器化 | 后续可补充正式反向代理、缓存和观测策略 |
| 记忆页展示的是后端返回的原始 score | `file` 与 `vector` 后端 score 语义不同，UI 暂不额外解释 |
| 状态管理仍有复杂度 | 先维持轻量 hooks 分层；只有在缓存/并发需求继续上升时再评估 React Query |
