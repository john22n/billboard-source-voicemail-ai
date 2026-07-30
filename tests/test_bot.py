import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

import bot


class BotStartupTests(unittest.TestCase):
    def test_run_bot_requires_openai_api_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY is required"):
                asyncio.run(bot.run_bot(transport=None, runner_args=None))

    def test_finalize_call_logs_completion_before_submitting_lead(self) -> None:
        events = []
        submit = AsyncMock(side_effect=lambda *_: events.append("nutshell"))

        with (
            patch.object(
                bot,
                "write_audit_event",
                side_effect=lambda event: events.append(event),
            ),
            patch.object(bot, "submit_nutshell_lead", new=submit),
        ):
            asyncio.run(bot.finalize_call(SimpleNamespace(state={})))

        self.assertEqual(events, ["call_completed", "nutshell"])
        submit.assert_awaited_once_with({}, ANY)


if __name__ == "__main__":
    unittest.main()
