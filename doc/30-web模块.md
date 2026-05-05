# Web 模块

Web 是 Synapse 的操作入口，提供普通用户聊天视图和管理员运维视图。当前 Web 使用 Vite 本地启动，未被 [docker-compose.yml](../docker-compose.yml) 编排。

## 关键文件

| 文件 | 说明 |
|---|---|
| [App.tsx](../apps/web/src/App.tsx) | 主要页面、状态、API 调用和 SSE 逻辑 |
| [App.css](../apps/web/src/App.css) | 页面样式 |
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

## 视图模式

| 模式 | 说明 |
|---|---|
| `client` | 普通用户聊天入口，按会话组织任务 |
| `ops` | 管理员运维台，管理任务、审批、取消、死信和事件流 |

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
| Agent 轨迹 | 展示工具轨迹、浏览结果、记忆命中、审批状态、最终回答 |
| 审批恢复 | paused 任务可 approve 并恢复执行 |
| 取消 | 支持单任务取消和批量取消 |
| 死信 | 管理员查看死信并重放 |
| 记忆 | 通过事件展示 memory_recall/memory_write，当前没有完整记忆管理页面 |

## 数据刷新

| 数据 | 策略 |
|---|---|
| 健康状态 | 10 秒轮询 |
| 任务列表 | 4 秒轮询 |
| 当前任务详情 | 1.5 秒轮询，作为 SSE 兜底 |
| 死信列表 | 5 秒轮询，仅运维视图 |
| SSE | 选中任务后连接，收到 terminal 后关闭 |

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

## 权限行为

1. 非管理员不能进入运维视图。
2. 普通用户只能看到自己的任务。
3. 管理员可查看全局任务和死信。
4. 后端是权限最终裁决方，前端只做体验层控制。

## 当前限制

| 限制 | 建议 |
|---|---|
| `App.tsx` 较大 | 拆分 auth/chat/ops/agent/workbench hooks 和组件 |
| 没有前端测试 | 增加关键交互 E2E 和组件测试 |
| 未容器化 | 增加 Web Dockerfile 或静态托管说明 |
| 没有完整记忆管理页面 | 可增加记忆列表、召回、删除 UI |
| 状态管理复杂 | 可引入 React Query 等数据层 |
