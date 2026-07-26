AGENT_INFO_SCHEMA = "synapse.agent.info.v1"
AGENT_EVENT_V2_SCHEMA = "synapse.agent.event.v2"

LEGACY_AGENT_INFO_EVENT_TYPES = frozenset(
    {
        "perceive",
        "memory_recall",
        "plan",
        "resume_started",
        "act",
        "tool_selected",
        "decide",
        "tool_started",
        "tool_finished",
        "tool_failed",
        "tool_skipped",
        "policy_blocked",
        "approval_required",
        "paused",
        "observe",
        "reflect",
        "replan",
        "synthesis_mode",
        "synthesis_failed",
        "memory_write",
        "evaluate",
        "diagnostic",
    }
)

TOOL_OUTPUT_PREVIEW_CHARS = 600
MEMORY_PREVIEW_CHARS = 240
SYNTHESIS_ERROR_PREVIEW_CHARS = 220
