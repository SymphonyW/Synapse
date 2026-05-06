import asyncio
import json
import unittest
from typing import Iterator

from app.runtime import (
    MODEL_MESSAGES_METADATA_KEY,
    AgentRuntime,
    OPENAI_DONE_MARKER,
    OpenAIStreamItem,
)


class ScriptedOpenAIRuntime(AgentRuntime):
    def __init__(
        self,
        rounds: list[list[OpenAIStreamItem]],
        continuation_max_rounds: int = 2,
        long_form_min_chars: int = 0,
    ) -> None:
        super().__init__(
            model_provider="openai",
            openai_api_key="test-key",
            openai_continuation_max_rounds=continuation_max_rounds,
            openai_long_form_min_chars=long_form_min_chars,
        )
        self.rounds = rounds
        self.calls: list[tuple[str, dict[str, str]]] = []

    def _request_openai_stream_with_retry(
        self,
        prompt: str,
        metadata: dict[str, str] | None = None,
    ) -> Iterator[OpenAIStreamItem]:
        self.calls.append((prompt, dict(metadata or {})))
        round_items = self.rounds.pop(0)
        for item in round_items:
            yield item


async def _collect_openai_text(runtime: AgentRuntime, prompt: str) -> str:
    chunks: list[str] = []
    async for chunk in runtime._run_openai(prompt):
        chunks.append(chunk)
    return "".join(chunks)


class OpenAIContinuationTests(unittest.TestCase):
    def test_continues_when_finish_reason_is_length(self) -> None:
        runtime = ScriptedOpenAIRuntime(
            [
                [
                    OpenAIStreamItem(content="First part"),
                    OpenAIStreamItem(finish_reason="length"),
                ],
                [
                    OpenAIStreamItem(content=f" continues to the end.{OPENAI_DONE_MARKER}"),
                    OpenAIStreamItem(finish_reason="stop"),
                ],
            ],
            continuation_max_rounds=2,
        )

        text = asyncio.run(_collect_openai_text(runtime, "explain in detail"))

        self.assertEqual(text, "First part continues to the end.")
        self.assertEqual(len(runtime.calls), 2)
        continuation_messages = json.loads(
            runtime.calls[1][1][MODEL_MESSAGES_METADATA_KEY]
        )
        self.assertEqual(continuation_messages[-2]["role"], "assistant")
        self.assertIn("First part", continuation_messages[-2]["content"])
        self.assertIn(OPENAI_DONE_MARKER, continuation_messages[-1]["content"])

    def test_long_form_request_continues_until_done_marker(self) -> None:
        first = (
            "Modern history overview: "
            + "context, causes, reform, revolution, and war. " * 20
            + "The final section begins"
        )
        runtime = ScriptedOpenAIRuntime(
            [
                [
                    OpenAIStreamItem(content=first),
                    OpenAIStreamItem(finish_reason="stop"),
                ],
                [
                    OpenAIStreamItem(
                        content=f" and is completed here.{OPENAI_DONE_MARKER}"
                    ),
                    OpenAIStreamItem(finish_reason="stop"),
                ],
            ],
            continuation_max_rounds=1,
        )

        text = asyncio.run(_collect_openai_text(runtime, "write a detailed history overview"))

        self.assertTrue(text.endswith("is completed here."))
        self.assertNotIn(OPENAI_DONE_MARKER, text)
        self.assertEqual(len(runtime.calls), 2)

    def test_ignores_done_marker_until_long_form_budget_is_met(self) -> None:
        runtime = ScriptedOpenAIRuntime(
            [
                [
                    OpenAIStreamItem(content=f"Too short.{OPENAI_DONE_MARKER}"),
                    OpenAIStreamItem(finish_reason="stop"),
                ],
                [
                    OpenAIStreamItem(
                        content=f" More detail satisfies the configured budget.{OPENAI_DONE_MARKER}"
                    ),
                    OpenAIStreamItem(finish_reason="stop"),
                ],
            ],
            continuation_max_rounds=1,
            long_form_min_chars=30,
        )

        text = asyncio.run(_collect_openai_text(runtime, "write a detailed overview"))

        self.assertEqual(text, "Too short. More detail satisfies the configured budget.")
        self.assertEqual(len(runtime.calls), 2)

    def test_ignores_done_marker_after_incomplete_sentence(self) -> None:
        runtime = ScriptedOpenAIRuntime(
            [
                [
                    OpenAIStreamItem(
                        content=f"This answer is long enough but ends with{OPENAI_DONE_MARKER}"
                    ),
                    OpenAIStreamItem(finish_reason="stop"),
                ],
                [
                    OpenAIStreamItem(content=f" a complete sentence.{OPENAI_DONE_MARKER}"),
                    OpenAIStreamItem(finish_reason="stop"),
                ],
            ],
            continuation_max_rounds=1,
            long_form_min_chars=10,
        )

        text = asyncio.run(_collect_openai_text(runtime, "write a detailed overview"))

        self.assertEqual(text, "This answer is long enough but ends with a complete sentence.")
        self.assertEqual(len(runtime.calls), 2)

    def test_long_form_continues_after_sensitive_finish_with_visible_text(self) -> None:
        runtime = ScriptedOpenAIRuntime(
            [
                [
                    OpenAIStreamItem(content="Long answer starts but is interrupted"),
                    OpenAIStreamItem(finish_reason="sensitive"),
                ],
                [
                    OpenAIStreamItem(content=f" and then finishes.{OPENAI_DONE_MARKER}"),
                    OpenAIStreamItem(finish_reason="stop"),
                ],
            ],
            continuation_max_rounds=1,
        )

        text = asyncio.run(_collect_openai_text(runtime, "write a detailed overview"))

        self.assertEqual(text, "Long answer starts but is interrupted and then finishes.")
        self.assertEqual(len(runtime.calls), 2)

    def test_short_sensitive_finish_does_not_continue(self) -> None:
        runtime = ScriptedOpenAIRuntime(
            [
                [
                    OpenAIStreamItem(content="Partial answer"),
                    OpenAIStreamItem(finish_reason="sensitive"),
                ],
            ],
            continuation_max_rounds=1,
        )

        text = asyncio.run(_collect_openai_text(runtime, "short answer"))

        self.assertEqual(text, "Partial answer")
        self.assertEqual(len(runtime.calls), 1)

    def test_does_not_continue_completed_answer(self) -> None:
        runtime = ScriptedOpenAIRuntime(
            [
                [
                    OpenAIStreamItem(content="完整回答。"),
                    OpenAIStreamItem(finish_reason="stop"),
                ],
            ],
            continuation_max_rounds=2,
        )

        text = asyncio.run(_collect_openai_text(runtime, "简单回答"))

        self.assertEqual(text, "完整回答。")
        self.assertEqual(len(runtime.calls), 1)


if __name__ == "__main__":
    unittest.main()
