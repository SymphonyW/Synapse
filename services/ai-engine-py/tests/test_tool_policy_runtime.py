import unittest

from app.runtime import AgentRuntime


class ToolPolicyRuntimeTests(unittest.TestCase):
    def test_runtime_reports_effective_policy_and_tool_metadata(self) -> None:
        runtime = AgentRuntime(
            model_provider="mock",
            agent_tool_policy_json='{"role_allow":{"user":["calculator"],"admin":["*"]},"approval_required":["calculator"],"disabled_tools":["browser_fetch"]}',
            agent_tool_audit_log_file="",
        )

        policy = runtime.current_tool_policy()
        self.assertEqual(policy.role_allow["user"], {"calculator"})
        self.assertEqual(policy.approval_required, {"calculator"})
        self.assertIn("browser_fetch", policy.disabled_tools)

        descriptors = {item["name"]: item for item in runtime.list_tools()}
        self.assertTrue(descriptors["browser_fetch"]["currently_disabled"])
        self.assertEqual(descriptors["calculator"]["allowed_roles"], ["admin", "user"])
        self.assertTrue(descriptors["calculator"]["requires_approval"])

    def test_runtime_apply_tool_policy_overrides_bootstrap_env_policy(self) -> None:
        runtime = AgentRuntime(
            model_provider="mock",
            agent_tool_policy_json='{"role_allow":{"user":["calculator"],"admin":["*"]},"approval_required":["calculator"],"disabled_tools":["browser_fetch"]}',
            agent_tool_audit_log_file="",
        )

        applied = runtime.apply_tool_policy(
            {
                "role_allow": {"user": ["retrieval"], "admin": ["*"]},
                "approval_required": ["retrieval"],
                "disabled_tools": [],
                "version": 3,
                "updated_at_unix_ms": 123456,
                "updated_by": "admin",
                "description": "managed override",
            }
        )

        self.assertEqual(applied.version, 3)
        self.assertEqual(applied.updated_by, "admin")
        self.assertEqual(applied.role_allow["user"], {"retrieval"})
        self.assertEqual(applied.approval_required, {"retrieval"})
        self.assertNotIn("browser_fetch", applied.disabled_tools)
        self.assertTrue(applied.is_tool_allowed("user", "retrieval"))
        self.assertFalse(applied.is_tool_allowed("user", "calculator"))


if __name__ == "__main__":
    unittest.main()
