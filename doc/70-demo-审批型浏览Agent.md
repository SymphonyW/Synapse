# Demo：审批型浏览 Agent

这个场景演示 Synapse 最有辨识度的一条链路：Agent 想调用高风险浏览工具，系统先暂停，再由人工审批后恢复执行。

| 项 | 内容 |
|---|---|
| 场景目标 | 看到 `running -> paused -> queued/running -> completed` 的完整生命周期 |
| 建议模式 | `mock` provider 即可 |
| 依赖能力 | 任务生命周期、工具治理、审批恢复、SSE、Trace 工作台 |

## 前置配置

1. 启动完整环境：

```powershell
.\scripts\dev.ps1 -Task up
```

2. 打开 `http://127.0.0.1:5173`，用默认管理员 `admin / 123456` 登录。
3. 默认高风险浏览工具就会要求审批；如果你改过工具策略，请确认 `summarize_page` 仍在审批集合中。
4. 若你准备访问真实外部网页，建议显式设置 `SYNAPSE_AGENT_TOOL_HTTP_ALLOWLIST=example.com` 后再启动；这样 Demo 的网络边界更清楚。

## 提示词

```text
访问 https://example.com，概括页面内容，并给出来源链接。
```

## 预期任务过程

```mermaid
flowchart LR
    A[创建任务] --> B[Agent 规划]
    B --> C[选择 summarize_page]
    C --> D[触发 approval_required]
    D --> E[任务 paused]
    E --> F[管理员 Approve & Resume]
    F --> G[重新入队并继续执行]
    G --> H[完成回答]
```

1. 任务先进入 `queued/running`；
2. Runtime 选择 `summarize_page`；
3. 因为它是高风险工具，任务进入 `paused`；
4. 管理员在运维台审批后，任务重新入队；
5. Runtime 从审批点继续，而不是从头重跑；
6. 最终生成带来源链接的回答。

## 预期事件

至少应看到：

| 阶段 | 事件 |
|---|---|
| 执行前 | `started`、`info(perceive)`、`info(memory_recall)`、`info(plan)` |
| 暂停点 | `info(approval_required)`、`paused` |
| 恢复点 | `approval_granted`、`resume_requested` |
| 继续执行 | `info(tool_finished)`、`token` |
| 结束 | `completed`、`terminal` |

## Web 中应该看到什么

1. 用户视图里，任务会停在 `paused`；
2. 运维视图里，任务详情会出现审批入口；
3. Trace 工作台里，`approval_required` 会被高亮；
4. 审批后同一任务继续流动，事件不会断层；
5. 最终回答应包含来源 URL。

## 常见失败与排查

| 现象 | 优先检查 |
|---|---|
| 任务直接失败，没有暂停 | 工具策略是否把 `summarize_page` 禁用；是否被角色白名单挡住 |
| 审批后仍再次暂停 | `approved_tool_call` 是否与工具名、输入 URL、风险等级、恢复步点完全匹配 |
| 工具执行失败 | 目标域名是否在 allowlist 中；目标站点是否可达 |
| Web 看不到后续事件 | 先查 `/v1/tasks/{taskID}/events`，再看前端是否收到 `last_event_id` 之后的增量 |

延伸阅读：[审批暂停与恢复](44-功能-审批暂停与恢复.md)、[任务生命周期与事件流](41-功能-任务生命周期与事件流.md)。
