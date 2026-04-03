# Synapse 文档索引

本目录只记录当前代码已经实现的能力，不描述未落地的规划。

## 文档地图

| 文档 | 聚焦内容 | 适用角色 |
| --- | --- | --- |
| [project-beginner-guide.md](./project-beginner-guide.md) | 新手完整入门：技术栈、文件职责、端到端流程、排错路径 | 新同学、全角色 |
| [architecture.md](./architecture.md) | 组件边界、任务生命周期、重试/取消/死信机制 | 后端、架构、测试 |
| [api.md](./api.md) | HTTP、SSE、gRPC 契约与错误语义 | 前后端、联调、测试 |
| [configuration.md](./configuration.md) | 环境变量、默认值、Compose 变量矩阵 | 后端、运维 |
| [deployment.md](./deployment.md) | 本地开发、Compose 启动、验收与排障 | 开发、测试、运维 |

补充入口：

- 根文档：[../README.md](../README.md)
- 前端控制台文档：[../apps/web/README.md](../apps/web/README.md)

## 推荐阅读顺序

1. 新接手项目：先看 [architecture.md](./architecture.md)，再看 [api.md](./api.md)。
2. 零基础入门：先看 [project-beginner-guide.md](./project-beginner-guide.md)，再看 [architecture.md](./architecture.md)。
3. 联调接口：直接看 [api.md](./api.md) 和 [../apps/web/README.md](../apps/web/README.md)。
4. 部署排障：先看 [deployment.md](./deployment.md)，再查 [configuration.md](./configuration.md)。

## 全局约定

- 任务状态：`queued`、`running`、`completed`、`failed`、`canceled`。
- 时间字段：
  - `created_at`、`updated_at` 使用 RFC3339。
  - `emitted_at_unix_ms` 使用 Unix 毫秒时间戳。
- 所有错误返回统一为：

```json
{
  "error": "..."
}
```

## 文档维护原则

- 以源码行为为准，避免“文档领先代码”。
- 接口字段、默认值、状态码变更必须同步更新文档。
- 示例命令优先保证可直接运行，避免伪命令。