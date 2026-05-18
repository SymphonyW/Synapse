# Demo：OpenAPI 工具 Agent

这个场景演示“外部 API 不是绕过 Runtime 的黑盒调用，而是先被发现、再被治理、最后才被执行”的完整链路。

| 项 | 内容 |
|---|---|
| 场景目标 | 让 Agent 通过 OpenAPI spec 发现工具，并调用受控 HTTP executor |
| 当前支持 | path/query/header 参数、JSON body、JSON/text 响应、allowlist、鉴权、超时、响应大小限制 |
| 当前限制 | 复杂 style、cookie 参数、OAuth 流程仍待扩展 |

## 前置配置

为了让这个 Demo 可复制，建议先准备一个你自己的测试 API。下面给出最小 spec 示例，实际服务地址可换成你的测试环境：

```json
{
  "openapi": "3.0.3",
  "servers": [{"url": "https://api.example.com"}],
  "paths": {
    "/items/{item_id}": {
      "get": {
        "operationId": "getItem",
        "parameters": [
          {"name": "item_id", "in": "path", "required": true, "schema": {"type": "string"}}
        ]
      }
    }
  }
}
```

启动前至少配置：

```powershell
$env:SYNAPSE_OPENAPI_ENABLED = "true"
$env:SYNAPSE_OPENAPI_SPEC_FILE = "D:\\apis\\demo-openapi.json"
$env:SYNAPSE_AGENT_TOOL_HTTP_ALLOWLIST = "api.example.com"
```

如果 API 需要鉴权，可继续补：

```powershell
$env:SYNAPSE_OPENAPI_BEARER_TOKEN = "replace-with-secret"
# 或
$env:SYNAPSE_OPENAPI_API_KEY_HEADER = "X-API-Key"
$env:SYNAPSE_OPENAPI_API_KEY_VALUE = "replace-with-secret"
```

随后启动环境：

```powershell
.\scripts\dev.ps1 -Task up
```

> 如果你使用 Docker Compose，又想让容器访问宿主机上的本地 Mock API，请把 `servers[0].url` 改成容器可访问的地址，并把该主机加入 allowlist；不要直接照抄 `127.0.0.1`。

## 提示词

```text
请查询 item_id=demo-42 的详情，并用一句话总结关键字段。
```

## 预期任务过程

1. AI Engine 载入 spec，注册出 `openapi_getitem` 一类工具；
2. Runtime 仍先经过角色授权和审批策略；
3. 审批通过后，`OpenAPIHTTPExecutor` 负责真正发起 HTTP 请求；
4. 返回结果被整理成工具输出，再进入回答生成。

## 预期事件

| 阶段 | 事件 |
|---|---|
| 执行前 | `info(plan)`、`info(tool_selected)` |
| 审批 | 若该操作被策略要求审批，则出现 `info(approval_required)`、`paused` |
| 调用 | `info(tool_started)`、`info(tool_finished)` |
| 结束 | `completed` |

GET 操作通常风险较低；若你希望把治理链路也演出来，可以在工具策略中心把该 OpenAPI 工具显式加入审批集合。

## Web 中应该看到什么

1. 工具策略页能看到 provider 为 `openapi` 的工具；
2. Trace 工作台能显示该工具的选择、执行和结果；
3. 若要求审批，审批页与浏览 Demo 的恢复体验一致；
4. 最终回答应基于真实 API 返回，而不是仅复述 prompt。

## 常见失败与排查

| 现象 | 优先检查 |
|---|---|
| 工具没注册出来 | `SYNAPSE_OPENAPI_ENABLED`、spec 文件路径、JSON/YAML 是否可解析 |
| 返回 `openapi_host_not_allowed` | `servers[0].url` 的 host 是否精确落在 allowlist |
| 返回 `openapi_executor_missing` | 说明仍在旧式“只发现不执行”路径，检查当前分支与启动配置 |
| 调用超时或 5xx | 先验证目标 API 自身，再看超时、鉴权和响应大小配置 |
| Web 中找不到该工具 | 先在工具策略页确认 provider 名称，再检查是否被 `disabled_tools` 屏蔽 |

延伸阅读：[Agent 工具治理与审批策略](45-功能-Agent工具治理与审批策略.md)。
