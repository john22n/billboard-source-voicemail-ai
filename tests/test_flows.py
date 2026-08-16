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
    collect_first_name,
    collect_inquiry_type,
    collect_last_name,
    collect_phone,
    collect_property_company,
    confirm_lead_collection,
    create_billboard_location_node,
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

    def test_property_inquiry_routes_to_company_question(self) -> None:
        flow_manager = cast(FlowManager, SimpleNamespace(state={}))

        result, next_node = asyncio.run(
            collect_inquiry_type(flow_manager, "property question")
        )

        self.assertEqual(result["value"], "property")
        self.assertEqual(next_node.get("name"), "property_company")

    def test_property_company_transitions_to_google_maps_referral(self) -> None:
        flow_manager = cast(FlowManager, SimpleNamespace(state={}))

        _, next_node = asyncio.run(
            collect_property_company(flow_manager, "Lamar Advertising")
        )

        self.assertEqual(next_node.get("name"), "property_referral")
        prompt = next_node["task_messages"][0]["content"]
        self.assertIn("Lamar Advertising", prompt)
        self.assertIn("Google Maps", prompt)
        self.assertIn("Thanks for calling Billboard Source, goodbye", prompt)
        self.assertIn("Do not ask", prompt)
        self.assertEqual(next_node.get("post_actions"), [{"type": "end_conversation"}])

    def test_advertising_inquiry_requests_permission_before_first_name(self) -> None:
        flow_manager = cast(FlowManager, SimpleNamespace(state={}))

        _, intro_node = asyncio.run(
            collect_inquiry_type(flow_manager, "advertising")
        )
        _, first_name_node = asyncio.run(
            confirm_lead_collection(flow_manager, True)
        )

        self.assertEqual(intro_node.get("name"), "advertising_intro")
        self.assertEqual(first_name_node.get("name"), "first_name")

    def test_location_transitions_directly_to_email(self) -> None:
        flow_manager = cast(FlowManager, SimpleNamespace(state={}))

        _, next_node = asyncio.run(
            collect_billboard_location(flow_manager, "Denver, CO")
        )

        self.assertEqual(flow_manager.state["billboard_location"], "Denver, CO")
        self.assertEqual(next_node.get("name"), "email")

    def test_location_node_never_repeats_or_confirms_location(self) -> None:
        location_node = create_billboard_location_node()

        prompt = location_node["task_messages"][0]["content"]
        self.assertIn("do not ask for it again", prompt)
        self.assertIn("ask for the city and state once", prompt)
        self.assertIn("Do not repeat, confirm, or ask", prompt)

    def test_email_transitions_to_phone_confirmation(self) -> None:
        flow_manager = cast(
            FlowManager,
            SimpleNamespace(state={"phone": "+15551234567"}),
        )

        _, next_node = asyncio.run(
            collect_email(flow_manager, "caller@example.com")
        )

        self.assertEqual(flow_manager.state["email"], "caller@example.com")
        self.assertEqual(next_node.get("name"), "confirm_phone")
        self.assertEqual(flow_manager.state["phone"], "+15551234567")
        phone_prompt = next_node["task_messages"][0]["content"]
        self.assertIn("+15551234567", phone_prompt)

    def test_confirmed_phone_transitions_to_last_name(self) -> None:
        flow_manager = cast(
            FlowManager,
            SimpleNamespace(state={"phone": "+15551234567"}),
        )

        _, next_node = asyncio.run(collect_phone(flow_manager, "+15557654321"))

        self.assertEqual(flow_manager.state["phone"], "+15557654321")
        self.assertEqual(next_node.get("name"), "last_name")

    def test_volunteered_last_name_is_saved_during_first_name_collection(self) -> None:
        flow_manager = cast(FlowManager, SimpleNamespace(state={}))

        _, next_node = asyncio.run(
            collect_first_name(flow_manager, "Jane", "Smith")
        )

        self.assertEqual(flow_manager.state["first_name"], "Jane")
        self.assertEqual(flow_manager.state["last_name"], "Smith")
        self.assertEqual(flow_manager.state["name"], "Jane Smith")
        self.assertEqual(next_node.get("name"), "advertising_type")

    def test_confirmed_phone_skips_last_name_when_initially_provided(self) -> None:
        flow_manager = cast(
            FlowManager,
            SimpleNamespace(
                state={
                    "first_name": "Jane",
                    "last_name": "Smith",
                    "name": "Jane Smith",
                }
            ),
        )

        _, next_node = asyncio.run(collect_phone(flow_manager, "+15557654321"))

        self.assertEqual(next_node.get("name"), "summarize_call")

    def test_last_name_completes_name_and_transitions_to_summary(self) -> None:
        flow_manager = cast(
            FlowManager,
            SimpleNamespace(state={"first_name": "Jane"}),
        )

        _, next_node = asyncio.run(collect_last_name(flow_manager, "Smith"))

        self.assertEqual(flow_manager.state["name"], "Jane Smith")
        self.assertEqual(next_node.get("name"), "summarize_call")

    def test_generated_summary_announces_assigned_associate_and_ends(self) -> None:
        flow_manager = cast(
            FlowManager,
            SimpleNamespace(state={"first_name": "Jane"}),
        )
        create_lead = AsyncMock(
            return_value={
                "id": "6-leads",
                "assignee": {
                    "id": "3-users",
                    "name": "Alex",
                    "email": "alex@example.com",
                },
            }
        )

        with patch(
            "src.flows.voicemail.submit_nutshell_lead",
            new=create_lead,
        ):
            _, next_node = asyncio.run(
                save_call_summary(flow_manager, "Caller wants a Denver billboard.")
            )

        self.assertEqual(
            flow_manager.state["call_summary"],
            "Caller wants a Denver billboard.",
        )
        create_lead.assert_awaited_once_with({}, flow_manager)
        self.assertEqual(flow_manager.state["associate_name"], "Alex")
        self.assertEqual(flow_manager.state["associate_email"], "alex@example.com")
        self.assertEqual(next_node.get("name"), "associate_followup")
        prompt = next_node["task_messages"][0]["content"]
        self.assertIn("Alex", prompt)
        self.assertIn("alex@example.com", prompt)
        self.assertIn("will reach out soon", prompt)
        self.assertIn("Do not describe this as a transfer or handoff", prompt)
        self.assertEqual(
            next_node.get("post_actions"),
            [{"type": "end_conversation"}],
        )

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

    def test_nutshell_action_skips_property_inquiry(self) -> None:
        flow_manager = cast(
            FlowManager,
            SimpleNamespace(
                state={
                    "inquiry_type": "property",
                    "phone": "+15551234567",
                }
            ),
        )
        create_lead = AsyncMock(return_value={"id": "6-leads"})

        with patch(
            "src.flows.tools.create_nutshell_lead",
            new=create_lead,
        ):
            asyncio.run(submit_nutshell_lead({}, flow_manager))

        create_lead.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
