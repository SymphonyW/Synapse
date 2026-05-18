# Demo：记忆型助手

这个场景演示 Synapse 如何把“长期记忆”从一个黑盒概念变成可写、可查、可召回、可验证的工作流。

| 项 | 内容 |
|---|---|
| 场景目标 | 先写入记忆，再让 Agent 在新任务中召回并使用它 |
| 建议模式 | 默认 `file` backend 即可 |
| 依赖能力 | 长期记忆、记忆管理页、retrieval 工具、Trace 工作台 |

## 前置配置

1. 启动完整环境：

```powershell
.\scripts\dev.ps1 -Task up
```

2. 打开 Web，登录后进入“记忆”页。
3. 默认 `SYNAPSE_MEMORY_BACKEND=file` 已够用；如果你想演示语义召回，再改用 [向量长期记忆](49-功能-向量长期记忆.md)。

## 提示词

先手工写入一条记忆：

| 字段 | 建议值 |
|---|---|
| `summary` | `网关故障恢复偏好` |
| `content` | `用户偏好优先排查 retryable upstream failures，并先查看死信与 replay 记录。` |
| `importance` | `0.9` |

然后新建任务：

```text
请回忆我对网关故障恢复的偏好，并给出一个简短排查建议。
```

## 预期任务过程

1. 任务开始时先执行 memory recall；
2. `memory_recall` 命中刚写入的记录；
3. planner 选择 `retrieval` 工具读取已召回上下文；
4. Agent 把记忆内容编进最终回答；
5. 如果任务允许写回，结束后还会追加一次 `memory_write`。

## 预期事件

| 阶段 | 事件 |
|---|---|
| 执行前 | `info(perceive)`、`info(memory_recall)` |
| 工具调用 | `info(tool_selected)`、`info(tool_finished)`，工具名应为 `retrieval` |
| 收尾 | `info(memory_write)`（启用写回时）、`completed` |

## Web 中应该看到什么

1. 记忆页能看到刚写入的记录；
2. 执行 recall 测试时，能看到命中结果、score 和 matched terms；
3. Trace 工作台里能看到 `memory_recall` 命中数量；
4. 最终回答里应引用刚刚写入的偏好，而不是泛泛而谈。

## 常见失败与排查

| 现象 | 优先检查 |
|---|---|
| 没有命中 | query 是否和记忆正文存在足够关键词重叠；`file` backend 更偏关键词，不是语义检索 |
| 命中了但回答没用上 | 查看 Trace 中是否真的执行了 `retrieval` |
| 记忆页为空 | 是否登录了正确用户；管理员是否误查了别的 `user_id` |
| vector 模式启动失败 | 检查 embedding 凭据、维度、数据库连接和 pgvector 初始化 |

延伸阅读：[向量长期记忆](49-功能-向量长期记忆.md)、[Agent Trace 工作台](47-功能-Agent-Trace工作台.md)。
