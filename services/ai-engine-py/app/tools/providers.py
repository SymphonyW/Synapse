import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol

from app.tools.base import AgentTool, BaseAgentTool, RiskLevel, ToolCall, ToolContext, ToolResult


ToolExecutor = Callable[[ToolCall, ToolContext], ToolResult]
OpenAPIOperationExecutor = Callable[[dict[str, Any], ToolCall, ToolContext], ToolResult]


@dataclass(frozen=True)
class ToolProviderPolicy:
    """工具来源声明的默认治理策略。

    provider 只声明默认值，最终是否可用仍由 ToolPolicy 统一合并和覆盖。
    这样插件接入不会绕过 Gateway/Runtime 已有的角色、审批和禁用逻辑。
    """

    role_allow: dict[str, set[str]] = field(default_factory=dict)
    approval_required: set[str] = field(default_factory=set)
    disabled_tools: set[str] = field(default_factory=set)


class ToolProvider(Protocol):
    """插件化工具来源的最小接口。

    本地 Python class、OpenAPI 描述和未来 MCP adapter 都只需要完成工具发现，
    registry 负责校验 schema 并把工具交给统一策略层。
    """

    provider_name: str

    def discover_tools(self) -> tuple[AgentTool, ...]:
        ...

    def policy_defaults(self) -> ToolProviderPolicy:
        ...


class LocalClassToolProvider:
    """把本地 Python 工具类或实例注册为 provider。

    该 provider 不强制工具继承 BaseAgentTool，只要求满足 AgentTool 协议，
    因此现有内置工具和后续业务自定义工具都可以复用同一条路径。
    """

    def __init__(
        self,
        tools: Iterable[type[AgentTool] | AgentTool],
        provider_name: str = "local_python",
        default_role_allow: dict[str, set[str]] | None = None,
        default_approval_required: set[str] | None = None,
    ) -> None:
        self.provider_name = _normalize_provider_name(provider_name)
        self._raw_tools = tuple(tools)
        self._policy = ToolProviderPolicy(
            role_allow=_copy_role_allow(default_role_allow or {}),
            approval_required={
                _normalize_tool_name(name)
                for name in (default_approval_required or set())
                if _normalize_tool_name(name)
            },
        )

    def discover_tools(self) -> tuple[AgentTool, ...]:
        tools: list[AgentTool] = []
        for raw_tool in self._raw_tools:
            # 支持传入类或实例：类会在发现阶段零参数实例化，实例则原样复用。
            if isinstance(raw_tool, type):
                tools.append(raw_tool())
            else:
                tools.append(raw_tool)
        return tuple(tools)

    def policy_defaults(self) -> ToolProviderPolicy:
        return self._policy


class OpenAPITool(BaseAgentTool):
    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        operation: dict[str, Any],
        executor: OpenAPIOperationExecutor | None,
        risk_level: RiskLevel,
        requires_approval: bool,
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.operation = operation
        self._executor = executor
        self.risk_level = risk_level
        self.requires_approval = requires_approval

    def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        # 第九阶段只完成 schema 注册和策略接入；真实 HTTP 调用由后续执行器注入，
        # 避免 OpenAPI 工具在未治理网络边界前直接联网。
        if self._executor is None:
            return ToolResult.failure(
                f"openapi tool {self.name} is registered but no executor is configured",
                code="openapi_executor_missing",
                details={
                    "operation_id": str(self.operation.get("operation_id", "")),
                    "method": str(self.operation.get("method", "")),
                    "path": str(self.operation.get("path", "")),
                },
            )

        return self._executor(dict(self.operation), call, context)


class OpenAPIToolProvider:
    """从 OpenAPI 描述发现工具。

    当前只解析 paths/operationId/parameters/requestBody，足够完成工具发现、
    schema 注册和权限策略接入；认证、真实请求和复杂参数序列化后续单独扩展。
    """

    def __init__(
        self,
        spec: dict[str, Any],
        provider_name: str = "openapi",
        name_prefix: str = "openapi",
        default_role_allow: dict[str, set[str]] | None = None,
        executor: OpenAPIOperationExecutor | None = None,
    ) -> None:
        self.provider_name = _normalize_provider_name(provider_name)
        self._spec = dict(spec)
        self._name_prefix = _normalize_tool_name(name_prefix)
        self._executor = executor
        self._default_role_allow = _copy_role_allow(default_role_allow or {})

    def discover_tools(self) -> tuple[AgentTool, ...]:
        paths = self._spec.get("paths", {})
        if not isinstance(paths, dict):
            return ()

        discovered: list[AgentTool] = []
        for path, path_item in paths.items():
            if not isinstance(path, str) or not isinstance(path_item, dict):
                continue

            path_parameters = path_item.get("parameters", [])
            for method, operation in path_item.items():
                normalized_method = str(method).strip().lower()
                if normalized_method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                if not isinstance(operation, dict):
                    continue

                operation_id = str(operation.get("operationId", "")).strip()
                raw_name = operation_id or f"{normalized_method}_{path}"
                tool_name = self._prefixed_name(raw_name)
                risk_level = _operation_risk_level(normalized_method, operation)
                requires_approval = _operation_requires_approval(risk_level, operation)
                operation_record = {
                    "operation_id": operation_id,
                    "method": normalized_method.upper(),
                    "path": path,
                }

                discovered.append(
                    OpenAPITool(
                        name=tool_name,
                        description=_operation_description(normalized_method, path, operation),
                        input_schema=_operation_input_schema(path_parameters, operation),
                        operation=operation_record,
                        executor=self._executor,
                        risk_level=risk_level,
                        requires_approval=requires_approval,
                    )
                )

        return tuple(discovered)

    def policy_defaults(self) -> ToolProviderPolicy:
        return ToolProviderPolicy(role_allow=_copy_role_allow(self._default_role_allow))

    def _prefixed_name(self, raw_name: str) -> str:
        normalized = _normalize_tool_name(raw_name) or "operation"
        if not self._name_prefix:
            return normalized
        if normalized.startswith(f"{self._name_prefix}_"):
            return normalized
        return f"{self._name_prefix}_{normalized}"


class MCPAdapter(Protocol):
    """未来 MCP 接入的窄接口。

    这里不绑定具体 MCP SDK，只规定 runtime 需要的最小能力：
    列出工具描述，并在执行时把 ToolCall 转给 adapter。
    """

    def list_tools(self) -> Iterable[dict[str, Any]]:
        ...

    def execute_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        ...


class MCPAdapterTool(BaseAgentTool):
    def __init__(
        self,
        adapter: MCPAdapter,
        remote_name: str,
        local_name: str,
        description: str,
        input_schema: dict[str, Any],
        risk_level: RiskLevel,
        requires_approval: bool,
    ) -> None:
        self._adapter = adapter
        self._remote_name = remote_name
        self.name = local_name
        self.description = description
        self.input_schema = input_schema
        self.risk_level = risk_level
        self.requires_approval = requires_approval

    def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        arguments = dict(call.arguments)
        if not arguments and call.input_text.strip():
            arguments = {"input": call.input_text.strip()}
        return self._adapter.execute_tool(self._remote_name, arguments, context)


class MCPToolProvider:
    """MCP adapter 的注册壳。

    真实连接、鉴权和 transport 后续放在 adapter 内；provider 只负责把 MCP 工具
    转成 AgentTool，确保治理和审计仍走现有 Runtime。
    """

    def __init__(
        self,
        adapter: MCPAdapter,
        provider_name: str = "mcp",
        name_prefix: str = "mcp",
        default_role_allow: dict[str, set[str]] | None = None,
    ) -> None:
        self.provider_name = _normalize_provider_name(provider_name)
        self._adapter = adapter
        self._name_prefix = _normalize_tool_name(name_prefix)
        self._default_role_allow = _copy_role_allow(default_role_allow or {})

    def discover_tools(self) -> tuple[AgentTool, ...]:
        discovered: list[AgentTool] = []
        for descriptor in self._adapter.list_tools():
            if not isinstance(descriptor, dict):
                continue

            remote_name = _normalize_tool_name(str(descriptor.get("name", "")))
            if not remote_name:
                continue

            local_name = self._prefixed_name(remote_name)
            input_schema = descriptor.get("input_schema") or descriptor.get("schema")
            if not isinstance(input_schema, dict) or not input_schema:
                input_schema = {"type": "object", "properties": {}, "additionalProperties": True}

            risk_level = _normalize_risk_level(str(descriptor.get("risk_level", ""))) or "high"
            requires_approval = descriptor.get("requires_approval")
            if not isinstance(requires_approval, bool):
                requires_approval = risk_level in {"high", "critical"}

            discovered.append(
                MCPAdapterTool(
                    adapter=self._adapter,
                    remote_name=remote_name,
                    local_name=local_name,
                    description=str(descriptor.get("description", "")).strip()
                    or f"MCP tool {remote_name}",
                    input_schema=input_schema,
                    risk_level=risk_level,
                    requires_approval=requires_approval,
                )
            )

        return tuple(discovered)

    def policy_defaults(self) -> ToolProviderPolicy:
        return ToolProviderPolicy(role_allow=_copy_role_allow(self._default_role_allow))

    def _prefixed_name(self, raw_name: str) -> str:
        if not self._name_prefix:
            return raw_name
        if raw_name.startswith(f"{self._name_prefix}_"):
            return raw_name
        return f"{self._name_prefix}_{raw_name}"


def _operation_description(method: str, path: str, operation: dict[str, Any]) -> str:
    summary = str(operation.get("summary", "")).strip()
    if summary:
        return summary
    description = str(operation.get("description", "")).strip()
    if description:
        return description
    return f"{method.upper()} {path}"


def _operation_input_schema(
    path_parameters: Any,
    operation: dict[str, Any],
) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []

    for parameter in _iter_openapi_parameters(path_parameters):
        _append_parameter_schema(parameter, properties, required)
    for parameter in _iter_openapi_parameters(operation.get("parameters", [])):
        _append_parameter_schema(parameter, properties, required)

    request_body = operation.get("requestBody")
    if isinstance(request_body, dict):
        content = request_body.get("content", {})
        body_schema: dict[str, Any] = {"type": "object"}
        if isinstance(content, dict):
            json_content = content.get("application/json")
            if isinstance(json_content, dict) and isinstance(json_content.get("schema"), dict):
                body_schema = json_content["schema"]
        properties["body"] = body_schema
        if bool(request_body.get("required", False)):
            required.append("body")

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = sorted(set(required))
    return schema


def _iter_openapi_parameters(raw_parameters: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(raw_parameters, list):
        return ()
    return tuple(item for item in raw_parameters if isinstance(item, dict))


def _append_parameter_schema(
    parameter: dict[str, Any],
    properties: dict[str, Any],
    required: list[str],
) -> None:
    name = str(parameter.get("name", "")).strip()
    if not name:
        return

    schema = parameter.get("schema")
    if not isinstance(schema, dict) or not schema:
        schema = {"type": "string"}
    if parameter.get("description") and "description" not in schema:
        schema = {**schema, "description": str(parameter["description"])}

    properties[name] = schema
    if bool(parameter.get("required", False)):
        required.append(name)


def _operation_risk_level(method: str, operation: dict[str, Any]) -> RiskLevel:
    declared = _normalize_risk_level(str(operation.get("x-synapse-risk-level", "")))
    if declared:
        return declared
    if method in {"post", "put", "patch", "delete"}:
        return "high"
    return "medium"


def _operation_requires_approval(risk_level: RiskLevel, operation: dict[str, Any]) -> bool:
    declared = operation.get("x-synapse-requires-approval")
    if isinstance(declared, bool):
        return declared
    return risk_level in {"high", "critical"}


def _normalize_provider_name(value: str) -> str:
    return _normalize_tool_name(value) or "provider"


def _normalize_tool_name(value: str) -> str:
    camel_spaced = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value.strip())
    camel_spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", camel_spaced)
    normalized = re.sub(r"[^a-z0-9_]+", "_", camel_spaced.lower())
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized


def _normalize_risk_level(value: str) -> RiskLevel | None:
    normalized = value.strip().lower()
    if normalized in {"low", "medium", "high", "critical"}:
        return normalized  # type: ignore[return-value]
    return None


def _copy_role_allow(role_allow: dict[str, set[str]]) -> dict[str, set[str]]:
    copied: dict[str, set[str]] = {}
    for role, tools in role_allow.items():
        normalized_role = str(role).strip().lower()
        if not normalized_role:
            continue
        copied[normalized_role] = {
            _normalize_tool_name(tool)
            for tool in tools
            if _normalize_tool_name(tool)
        }
    return copied
