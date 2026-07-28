# 功能：Replay 对比与 Trace Diff

## 1. 目标

让开发者或管理员能够比较原任务与 replay 子任务在执行轨迹、工具选择、审批、重规划和最终结果上的差异，而不是只看到“又跑了一次”。

## 2. 关系建模

| 字段 | 说明 |
|---|---|
| `replay_of_task_id` | replay 子任务指向源任务的正式字段 |

设计选择：

1. replay 采用“创建子任务”而不是“重置原任务”；
2. 原任务通过 `GET /v1/tasks/{taskID}/replays` 获取 replay 列表；
3. 这样可以保留原 trace，避免比较基线被覆盖。

## 3. API

| 接口 | 说明 |
|---|---|
| `GET /v1/tasks/{taskID}/replays` | 返回 replay 子任务列表 |
| `GET /v1/tasks/{taskID}/compare/{otherTaskID}` | 返回源任务与 replay 子任务的任务快照、事件快照和截断标记 |

权限规则：

1. 普通用户只能访问自己的任务；
2. 管理员可跨用户排障；
3. compare 只允许源任务与其直接 replay 子任务比较，拒绝无关任务越权拼接。

## 4. Diff 维度

1. 任务状态；
2. 总耗时；
3. 计划步骤数；
4. 工具调用序列；
5. 工具调用成功/失败；
6. 是否发生 `approval_required`；
7. 是否发生 `replan`；
8. `memory_recall` 命中数量；
9. `evaluate` 指标；
10. 最终回答文本长度；
11. 最终回答段落级 diff。

## 5. 前端实现

1. 继续复用 `apps/web/src/features/trace/traceParser.ts`；
2. `traceDiff.ts` 只负责比较两个已解析 trace；
3. `ReplayDiffPanel.tsx` 负责 replay 列表、左右并排摘要、可折叠阶段、工具序列和文本 diff；
4. 当 trace 缺失阶段或事件被截断时，面板仍展示已知内容并提示不完整。

## 6. 存储与迁移

Replay 相关 schema 已纳入 Gateway migration：

1. `000001_initial_schema.up.sql` 创建 `tasks.replay_of_task_id`。
2. `000002_task_replay_indexes.up.sql` 创建 `(replay_of_task_id, created_at DESC)` 索引。

旧 `ensureSchema` 数据库若结构已经匹配 v4，可用 `migrate-baseline -Version 4` 记录版本，再执行 `migrate-up` 升级到当前 schema。
