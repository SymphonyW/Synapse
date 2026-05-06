import os
import unittest
from unittest.mock import patch

from app.config import load_config


class ConfigTests(unittest.TestCase):
    def test_loads_openai_continuation_and_agent_timeout_settings(self) -> None:
        with patch.dict(
            os.environ,
            {
                "SYNAPSE_OPENAI_CONTINUATION_MAX_ROUNDS": "3",
                "SYNAPSE_OPENAI_LONG_FORM_MIN_CHARS": "3200",
                "SYNAPSE_AGENT_GENERATION_TIMEOUT_SECONDS": "180",
                "SYNAPSE_AGENT_STREAM_IDLE_TIMEOUT_SECONDS": "45",
            },
            clear=True,
        ):
            config = load_config()

        self.assertEqual(config.openai_continuation_max_rounds, 3)
        self.assertEqual(config.openai_long_form_min_chars, 3200)
        self.assertEqual(config.agent_generation_timeout_seconds, 180)
        self.assertEqual(config.agent_stream_idle_timeout_seconds, 45)


if __name__ == "__main__":
    unittest.main()
