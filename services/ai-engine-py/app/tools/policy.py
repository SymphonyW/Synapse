import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolPolicy:
    role_allow: dict[str, set[str]]
    approval_required: set[str]
    disabled_tools: set[str]

    @classmethod
    def from_json(
        cls,
        raw_json: str,
        default_role_allow: dict[str, set[str]],
        default_approval_required: set[str],
        default_disabled_tools: set[str] | None = None,
    ) -> "ToolPolicy":
        role_allow = {
            role: set(tools)
            for role, tools in default_role_allow.items()
        }
        approval_required = set(default_approval_required)
        # provider 可以声明默认禁用项；部署侧 JSON 仍然可以覆盖或补充。
        disabled_tools: set[str] = set(default_disabled_tools or set())

        payload = {}
        raw = raw_json.strip()
        if raw:
            try:
                decoded = json.loads(raw)
                if isinstance(decoded, dict):
                    payload = decoded
            except json.JSONDecodeError:
                payload = {}

        roles_payload = payload.get("role_allow")
        if isinstance(roles_payload, dict):
            for role, tools in roles_payload.items():
                if not isinstance(role, str):
                    continue
                if not isinstance(tools, list):
                    continue

                normalized_role = role.strip().lower()
                if not normalized_role:
                    continue

                normalized_tools: set[str] = set()
                for tool in tools:
                    if isinstance(tool, str):
                        name = tool.strip().lower()
                        if name:
                            normalized_tools.add(name)

                role_allow[normalized_role] = normalized_tools

        approval_payload = payload.get("approval_required")
        if isinstance(approval_payload, list):
            approval_required = {
                tool.strip().lower()
                for tool in approval_payload
                if isinstance(tool, str) and tool.strip()
            }

        disabled_payload = payload.get("disabled_tools")
        if isinstance(disabled_payload, list):
            disabled_tools.update({
                tool.strip().lower()
                for tool in disabled_payload
                if isinstance(tool, str) and tool.strip()
            })

        return cls(
            role_allow=role_allow,
            approval_required=approval_required,
            disabled_tools=disabled_tools,
        )

    def is_tool_allowed(self, role: str, tool_name: str) -> bool:
        normalized_role = role.strip().lower() or "user"
        normalized_tool = tool_name.strip().lower()
        if not normalized_tool:
            return False

        if normalized_tool in self.disabled_tools:
            return False

        allowed = self.role_allow.get(normalized_role)
        if allowed is None:
            allowed = self.role_allow.get("user", set())

        if "*" in allowed:
            return True

        return normalized_tool in allowed

    def requires_approval(self, tool_name: str) -> bool:
        normalized_tool = tool_name.strip().lower()
        if normalized_tool in self.disabled_tools:
            return False
        return normalized_tool in self.approval_required
