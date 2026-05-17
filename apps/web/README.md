# Synapse Web Console

本文档对应 `apps/web` 当前实现，描述页面能力、数据流和运行方式。

## 1. 技术栈

- React 19
- TypeScript
- Vite

开发代理（`vite.config.ts`）：

- `/v1` -> `http://127.0.0.1:8080`
- `/healthz` -> `http://127.0.0.1:8080`

## 2. 启动与构建

在 `apps/web` 目录执行：

```bash
npm install
npm run dev
```

默认地址：`http://127.0.0.1:5173`

生产构建：

```bash
npm run build
```

测试：

```bash
npm run test
```

容器化：

```bash
# 生产静态模式
docker compose up --build -d web

# 容器内 Vite 开发模式
docker compose --profile web-dev up --build web-dev
```

生产模式通过 Nginx 托管 `dist`，并把 `/v1`、`/healthz` 反代到 `gateway`。

## 3. 视图与核心能力

前端提供四种视图模式，并保存在本地存储：

- `client`：用户端
- `memory`：长期记忆
- `ops`：运维端
- `policy`：工具策略

本地存储键：

- `synapse.web.language`
- `synapse.web.view-mode`
- `synapse.web.auth.session`（仅缓存当前会话身份摘要，不存储密码）

认证与权限：

- 登录与注册由网关接口提供（Cookie 会话）。
- `user` 角色只能访问自己的任务与事件流。
- `admin` 角色可访问运维台与死信面板。

### 3.1 用户端（client）

- 登录后按会话维度发起与查看任务（`POST /v1/tasks`）。
- 展示当前用户可见任务（普通用户仅本人，管理员可切换 `user_id`）。
- 查看选中任务事件流（SSE）。
- 中英文切换。

### 3.2 运维端（ops）

- 仅管理员可进入。
- 创建任务。
- 最近任务列表与状态过滤。
- 单任务取消。
- 批量取消（支持部分成功）。
- 批量取消历史记录（最多保留 8 条）。
- 复制批量取消失败任务 ID。
- 死信列表查询与重放。
- Agent Trace 工作台展示。

## 4. 轮询与流式策略

页面初始化后会同时启动定时刷新：

- 健康状态：每 10 秒
- 死信列表：每 5 秒
- 任务列表：每 4 秒
- 当前选中任务详情：每 1.5 秒（SSE 的兜底同步）

SSE 行为：

- 选中任务后连接 `/v1/tasks/{taskID}/events?last_event_id=<cursor>`。
- 支持 `last_event_id` 续传。
- 收到 `terminal` 时自动关闭连接。
- 若连接错误，状态置为 `closed`，等待用户切换任务或刷新后重连。

前端监听事件类型：

- `info`
- `started`
- `token`
- `cancel_requested`
- `canceled`
- `completed`
- `failed`
- `dead_lettered`
- `replay_requested`
- `terminal`
- `unspecified`

## 5. 调用的后端接口

- `GET /healthz`
- `POST /v1/auth/register`
- `POST /v1/auth/login`
- `POST /v1/auth/logout`
- `GET /v1/auth/me`
- `POST /v1/tasks`
- `GET /v1/tasks`
- `GET /v1/tasks/{taskID}`
- `POST /v1/tasks/{taskID}/cancel`
- `POST /v1/tasks/cancel`
- `POST /v1/tasks/{taskID}/replay`
- `GET /v1/tasks/{taskID}/events`
- `GET /v1/dead-letters`

## 6. 交互细节

- 创建任务后会自动选中该任务，重置本地事件窗口，并触发任务列表刷新。
- 批量取消后：
  - 成功项会即时更新到列表。
  - 失败项会保留选中，便于重复操作。
  - 顶部错误条会显示失败摘要。
- 任务列表按 `updated_at` 倒序展示。
- 运维端 Trace 工作台支持结构化视图 / 原始 JSON 切换、复制原始 JSON、导出当前任务 Trace。

## 7. 已知限制

- 会话采用单机 Cookie + 存储层会话表，暂未接入跨域场景下的 CSRF 防护策略。
- 普通用户任务列表当前由网关在通用列表结果上做权限过滤，尚未拆分为独立的按用户分页查询接口。

## 8. 工程结构

- `src/features/*`：按业务域拆分
- `src/shared/api`：统一 API client 与前端配置
- `src/shared/hooks`：健康检查与 SSE
- `src/shared/types`：共享领域类型
- `src/shared/components`：应用壳层组件
