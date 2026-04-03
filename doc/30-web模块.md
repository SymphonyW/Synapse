# Web 模块

## 1. 模块定位

Web 是 Synapse 的操作入口，提供“用户端聊天视图”和“管理员运维视图”。

关联文件：

1. [apps/web/src/App.tsx](../apps/web/src/App.tsx)
2. [apps/web/src/main.tsx](../apps/web/src/main.tsx)
3. [apps/web/vite.config.ts](../apps/web/vite.config.ts)
4. [apps/web/package.json](../apps/web/package.json)

## 2. 视图模式

1. client：普通用户主入口，聚焦多轮会话。
2. ops：管理员运维台，聚焦任务全局管理。

视图模式保存在 localStorage，键名：

1. synapse.web.view-mode
2. synapse.web.language
3. synapse.web.auth.session

## 3. 核心交互

1. 登录/注册/登出。
2. 创建任务并自动选中。
3. 会话维度消息展示（conversation_id）。
4. 任务状态实时更新与事件流可视化。
5. 单任务取消、批量取消。
6. 死信查看与重放。

## 4. 数据刷新策略

1. 健康状态：10 秒轮询。
2. 任务列表：4 秒轮询。
3. 选中任务详情：1.5 秒轮询兜底。
4. 死信列表：5 秒轮询（仅运维台）。

## 5. SSE 策略

1. 针对选中任务建立独立 EventSource。
2. 通过 last_event_id 续传。
3. 客户端基于 event_id 去重，避免重连重复展示。
4. 收到 terminal 自动关闭连接。

## 6. 权限行为

1. 非管理员强制停留 client 视图。
2. 普通用户无法进入运维台。
3. 后端依然是权限最终裁决方。

## 7. 当前限制

1. App.tsx 体量较大，职责较多。
2. 状态管理以 useState/useMemo 为主，复杂度已较高。
3. 尚未引入组件级自动化测试。

## 8. 演进建议

1. 按业务域拆分组件与 hooks（auth/task/chat/ops）。
2. 引入统一数据层（如 React Query）。
3. 增加关键交互 E2E 用例（创建任务、取消、重放）。
