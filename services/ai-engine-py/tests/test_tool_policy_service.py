import unittest

from app.runtime import AgentRuntime
from app.service import AgentRuntimeService
from synapse.v1 import agent_pb2


class ToolPolicyServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_apply_tool_policy_uses_python_proto_attributes(self) -> None:
        runtime = AgentRuntime(model_provider="mock", agent_tool_audit_log_file="")
        service = AgentRuntimeService(runtime)

        response = await service.ApplyToolPolicy(
            agent_pb2.ApplyToolPolicyRequest(
                policy=agent_pb2.ToolPolicy(
                    role_allow={
                        "user": agent_pb2.StringList(items=["retrieval"]),
                        "admin": agent_pb2.StringList(items=["*"]),
                    },
                    approval_required=["retrieval"],
                    disabled_tools=["calculator"],
                    version=7,
                    updated_at_unix_ms=123456,
                    updated_by="admin",
                    description="managed",
                )
            ),
            None,
        )

        self.assertTrue(response.applied)
        self.assertEqual(response.policy.version, 7)
        self.assertEqual(runtime.current_tool_policy().role_allow["user"], {"retrieval"})
        self.assertEqual(runtime.current_tool_policy().disabled_tools, {"calculator"})


if __name__ == "__main__":
    unittest.main()
