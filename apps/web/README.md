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

## 3. 视图与核心能力

前端提供两种视图模式，并保存在本地存储：

- `client`：用户端
- `ops`：运维端

本地存储键：

- `synapse.web.language`
- `synapse.web.view-mode`

### 3.1 用户端（client）

- 提交任务（`POST /v1/tasks`）。
- 展示当前 `user_id` 的任务列表（由全量任务列表前端过滤）。
- 查看选中任务事件流（SSE）。
- 中英文切换。

### 3.2 运维端（ops）

- 创建任务。
- 最近任务列表与状态过滤。
- 单任务取消。
- 批量取消（支持部分成功）。
- 批量取消历史记录（最多保留 8 条）。
- 复制批量取消失败任务 ID。
- 死信列表查询与重放。
- 实时事件流展示。

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

## 7. 已知限制

- 当前没有登录态和权限体系，`user_id` 只是请求字段。
- 用户端我的任务基于前端筛选，不是后端独立接口。
