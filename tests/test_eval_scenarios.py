import json
import unittest
from pathlib import Path

from pipecat.evals.scenario import EvalScenario
from pipecat.evals.suite import EvalManifest

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "scenarios"
MANIFEST = SCENARIOS / "suite.yml"


class EvalScenarioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = EvalManifest.load(MANIFEST)
        self.scenarios = [
            EvalScenario.load(run.scenario_path) for run in self.manifest.runs
        ]

    def test_suite_includes_every_voicemail_scenario(self) -> None:
        expected = {
            path.resolve()
            for path in SCENARIOS.glob("*.yml")
            if path.name != MANIFEST.name
        }
        configured = {run.scenario_path for run in self.manifest.runs}

        self.assertEqual(configured, expected)

    def test_every_scenario_checks_the_spoken_opening(self) -> None:
        for scenario in self.scenarios:
            with self.subTest(scenario=scenario.name):
                self.assertTrue(scenario.bot_audio)
                self.assertIsNone(scenario.turns[0].user)
                self.assertTrue(
                    any(
                        expectation.event == "tts_response"
                        and expectation.text_contains
                        and "Thanks for calling Billboard Source"
                        in expectation.text_contains
                        for expectation in scenario.turns[0].expect
                    )
                )

    def test_suite_covers_every_flow_function_and_branch(self) -> None:
        calls = [
            call
            for scenario in self.scenarios
            for turn in scenario.turns
            for expectation in turn.expect
            if expectation.event == "function_call"
            for call in expectation.calls or []
        ]
        call_names = {call.name for call in calls}

        self.assertEqual(
            call_names,
            {
                "collect_inquiry_type",
                "collect_business_lead",
                "confirm_callback_number",
                "collect_callback_number",
            },
        )

        inquiry_types = {
            call.args["inquiry_type"]
            for call in calls
            if call.name == "collect_inquiry_type" and call.args
        }
        callback_decisions = {
            call.args["is_good_callback"]
            for call in calls
            if call.name == "confirm_callback_number" and call.args
        }
        business_calls = [
            call for call in calls if call.name == "collect_business_lead"
        ]

        self.assertEqual(inquiry_types, {"advertising", "property"})
        self.assertEqual(callback_decisions, {True, False})
        self.assertTrue(any(call.args and "phone" in call.args for call in business_calls))
        self.assertTrue(any(call.args and "phone" not in call.args for call in business_calls))

    def test_every_function_branch_checks_its_spoken_response(self) -> None:
        for scenario in self.scenarios:
            for turn_index, turn in enumerate(scenario.turns):
                has_function_call = any(
                    expectation.event == "function_call"
                    for expectation in turn.expect
                )
                if not has_function_call:
                    continue

                with self.subTest(
                    scenario=scenario.name,
                    turn_index=turn_index,
                ):
                    self.assertTrue(
                        any(
                            expectation.event == "tts_response"
                            for expectation in turn.expect
                        )
                    )

    def test_incoming_callback_scenarios_use_the_eval_caller_number(self) -> None:
        configured = {
            run.scenario
            for run in self.manifest.runs
            if run.runner_body_path is not None
        }
        body = json.loads((SCENARIOS / "incoming_call.json").read_text())

        self.assertEqual(
            configured,
            {
                "advertising_incoming_callback_accepted",
                "advertising_incoming_callback_replaced",
            },
        )
        self.assertEqual(body, {"calling_phone": "+13135550144"})


if __name__ == "__main__":
    unittest.main()
