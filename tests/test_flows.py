import asyncio
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, patch

from pipecat.flows import FlowManager
from pipecat.processors.aggregators.llm_context import LLMContext

from src.flows.tools import (
    format_call_transcript,
    submit_nutshell_lead,
    write_audit_event,
)
from src.flows.voicemail import (
    collect_billboard_location,
    collect_email,
    create_end_node,
    save_call_summary,
)


class FlowTests(unittest.TestCase):
    def test_audit_log_persists_only_safe_submission_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "audit.jsonl")
            with patch.dict(os.environ, {"VOICEMAIL_AUDIT_LOG": path}):
                write_audit_event(
                    "nutshell_submission_failed",
                    error_type="ClientResponseError",
                    http_status=422,
                )

            with open(path, encoding="utf-8") as audit_log:
                event = json.loads(audit_log.read())

        self.assertEqual(event["event"], "nutshell_submission_failed")
        self.assertEqual(event["error_type"], "ClientResponseError")
        self.assertEqual(event["http_status"], 422)
        self.assertEqual(
            set(event),
            {"timestamp", "event", "error_type", "http_status"},
        )

    def test_location_lookup_populates_state_and_pricing_node(self) -> None:
        flow_manager = cast(FlowManager, SimpleNamespace(state={}))
        pricing = {
            "city": "Denver",
            "state": "CO",
            "avg_daily_views": "20,000",
            "four_week_range": "$2,000-$4,000",
        }

        with patch(
            "src.flows.voicemail.get_location_pricing",
            new=AsyncMock(return_value=pricing),
        ):
            result, next_node = asyncio.run(
                collect_billboard_location(flow_manager, "Denver, CO")
            )

        self.assertTrue(result["pricing_found"])
        self.assertEqual(flow_manager.state["location_pricing"], pricing)
        self.assertEqual(next_node.get("name"), "pricing_summary")
        pricing_prompt = next_node["task_messages"][0]["content"]
        self.assertIn("20,000", pricing_prompt)
        self.assertIn("$2,000-$4,000", pricing_prompt)

    def test_location_lookup_failure_still_collects_contact_info(self) -> None:
        flow_manager = cast(FlowManager, SimpleNamespace(state={}))

        with patch(
            "src.flows.voicemail.get_location_pricing",
            new=AsyncMock(side_effect=RuntimeError("database unavailable")),
        ):
            result, next_node = asyncio.run(
                collect_billboard_location(flow_manager, "Denver, CO")
            )

        self.assertFalse(result["pricing_found"])
        self.assertEqual(next_node.get("name"), "location_not_found")

    def test_email_transitions_to_summary_without_asking_for_phone(self) -> None:
        flow_manager = cast(
            FlowManager,
            SimpleNamespace(state={"phone": "+15551234567"}),
        )

        _, next_node = asyncio.run(
            collect_email(flow_manager, "caller@example.com")
        )

        self.assertEqual(flow_manager.state["email"], "caller@example.com")
        self.assertEqual(next_node.get("name"), "summarize_call")
        self.assertEqual(flow_manager.state["phone"], "+15551234567")

    def test_generated_summary_is_saved_before_goodbye(self) -> None:
        flow_manager = cast(FlowManager, SimpleNamespace(state={}))

        _, next_node = asyncio.run(
            save_call_summary(flow_manager, "Caller wants a Denver billboard.")
        )

        self.assertEqual(
            flow_manager.state["call_summary"],
            "Caller wants a Denver billboard.",
        )
        self.assertEqual(next_node.get("name"), "end")

    def test_end_node_closes_call_before_nutshell_submission(self) -> None:
        end_node = create_end_node()

        self.assertEqual(
            end_node.get("post_actions"),
            [{"type": "end_conversation"}],
        )

    def test_transcript_excludes_prompts_and_tool_messages(self) -> None:
        context = LLMContext(
            cast(
                Any,
                [
                    {"role": "developer", "content": "Internal instruction"},
                    {"role": "assistant", "content": "What is your name?"},
                    {"role": "user", "content": "Jane Smith"},
                    {"role": "tool", "content": "Tool result"},
                ],
            )
        )

        self.assertEqual(
            format_call_transcript(context),
            "Assistant: What is your name?\nCaller: Jane Smith",
        )

    def test_nutshell_action_includes_twilio_phone(self) -> None:
        context = LLMContext(
            [
                {"role": "assistant", "content": "Where do you want to advertise?"},
                {"role": "user", "content": "Denver, Colorado."},
            ]
        )
        flow_manager = cast(
            FlowManager,
            SimpleNamespace(
                state={
                    "name": "Jane Smith",
                    "business_name": "Example Company",
                    "billboard_location": "Denver, CO",
                    "email": "jane@example.com",
                    "phone": "+15551234567",
                    "call_summary": "Caller wants a billboard in Denver.",
                    "llm_context": context,
                }
            ),
        )
        create_lead = AsyncMock(return_value={"id": "6-leads"})

        with patch(
            "src.flows.tools.create_nutshell_lead",
            new=create_lead,
        ):
            asyncio.run(submit_nutshell_lead({}, flow_manager))

        create_lead.assert_awaited_once()
        awaited_call = create_lead.await_args
        if awaited_call is None:
            self.fail("Expected the Nutshell lead call")
        lead = awaited_call.args[0]
        self.assertEqual(lead.phone, "+15551234567")
        self.assertEqual(lead.email, "jane@example.com")
        self.assertEqual(lead.billboard_location, "Denver, CO")
        self.assertEqual(lead.notes, "Caller wants a billboard in Denver.")
        self.assertEqual(
            lead.transcript,
            "Assistant: Where do you want to advertise?\n"
            "Caller: Denver, Colorado.",
        )

    def test_nutshell_action_submits_only_once(self) -> None:
        flow_manager = cast(
            FlowManager,
            SimpleNamespace(state={"phone": "+15551234567"}),
        )
        create_lead = AsyncMock(return_value={"id": "6-leads"})

        async def submit_twice() -> None:
            await asyncio.gather(
                submit_nutshell_lead({}, flow_manager),
                submit_nutshell_lead({}, flow_manager),
            )

        with patch(
            "src.flows.tools.create_nutshell_lead",
            new=create_lead,
        ):
            asyncio.run(submit_twice())

        create_lead.assert_awaited_once()
        awaited_call = create_lead.await_args
        if awaited_call is None:
            self.fail("Expected the phone-only Nutshell lead call")
        self.assertEqual(awaited_call.args[0].phone, "+15551234567")

    def test_nutshell_action_skips_lead_without_caller_details(self) -> None:
        flow_manager = cast(FlowManager, SimpleNamespace(state={}))
        create_lead = AsyncMock(return_value={"id": "6-leads"})

        with patch(
            "src.flows.tools.create_nutshell_lead",
            new=create_lead,
        ):
            asyncio.run(submit_nutshell_lead({}, flow_manager))

        create_lead.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
