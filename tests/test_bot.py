import asyncio
import os
import unittest
from unittest.mock import patch

import bot


class BotStartupTests(unittest.TestCase):
    def test_run_bot_requires_openai_api_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY is required"):
                asyncio.run(bot.run_bot(transport=None, runner_args=None))


if __name__ == "__main__":
    unittest.main()
