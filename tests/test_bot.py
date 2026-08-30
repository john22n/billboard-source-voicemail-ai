import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

import bot


class BotStartupTests(unittest.TestCase):
    def test_env_flag_parses_explicit_boolean_values(self) -> None:
        for value in ("1", "true", "TRUE", "yes", "on"):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {"TEST_FLAG": value},
            ):
                self.assertTrue(bot._env_flag("TEST_FLAG"))

        for value in ("0", "false", "FALSE", "no", "off", ""):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {"TEST_FLAG": value},
            ):
                self.assertFalse(bot._env_flag("TEST_FLAG"))

        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(bot._env_flag("TEST_FLAG"))
            self.assertTrue(bot._env_flag("TEST_FLAG", default=True))

    def test_run_bot_requires_openai_api_key(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY is required"),
        ):
            asyncio.run(bot.run_bot(transport=None, runner_args=None))

    def test_openai_stt_owns_turn_detection_for_short_responses(self) -> None:
        captured_stt = {}
        captured_aggregator = {}

        class PipelineConfigured(Exception):
            pass

        class FakeOpenAIRealtimeSTTService:
            Settings = bot.OpenAIRealtimeSTTService.Settings

            def __init__(self, **kwargs: object) -> None:
                captured_stt.update(kwargs)

        def capture_aggregator(*_: object, **kwargs: object) -> None:
            captured_aggregator.update(kwargs)
            raise PipelineConfigured

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test"}),
            patch.object(
                bot,
                "OpenAIRealtimeSTTService",
                FakeOpenAIRealtimeSTTService,
            ),
            patch.object(
                bot,
                "LLMContextAggregatorPair",
                side_effect=capture_aggregator,
            ),
            self.assertRaises(PipelineConfigured),
        ):
            asyncio.run(bot.run_bot(transport=None, runner_args=None))

        self.assertEqual(captured_stt["turn_detection"], {"type": "server_vad"})
        self.assertEqual(captured_stt["settings"].model, "gpt-4o-transcribe")
        self.assertNotIn("user_params", captured_aggregator)

    def test_luna_disables_reasoning_for_function_tools(self) -> None:
        captured_settings = []

        class LLMCreated(Exception):
            pass

        class FakeOpenAILLMService:
            Settings = bot.OpenAILLMService.Settings

            def __init__(self, *, settings: object, **_: object) -> None:
                captured_settings.append(settings)
                raise LLMCreated

        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test"}),
            patch.object(bot, "OpenAIRealtimeSTTService"),
            patch.object(bot, "OpenAILLMService", FakeOpenAILLMService),
            self.assertRaises(LLMCreated),
        ):
            asyncio.run(bot.run_bot(transport=None, runner_args=None))

        self.assertEqual(captured_settings[0].extra["reasoning_effort"], "none")

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
