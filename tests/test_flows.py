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
    collect_business_lead,
    collect_callback_number,
    collect_inquiry_type,
    confirm_callback_number,
    create_associate_followup_node,
    create_business_lead_node,
    create_callback_confirmation_node,
    create_callback_number_node,
    create_initial_node,
    create_property_end_node,
)


class FlowTests(unittest.TestCase):
    def test_fixed_speech_nodes_do_not_also_run_the_llm(self) -> None:
        nodes = [
            create_initial_node(),
            create_property_end_node(),
            create_business_lead_node(),
            create_callback_confirmation_node(
                "+15551234567",
                uses_calling_phone=True,
            ),
            create_associate_followup_node(None, None),
        ]

        for node in nodes:
            with self.subTest(node=node["name"]):
                self.assertIs(node.get("respond_immediately"), False)

        self.assertEqual(
            nodes[1]["pre_actions"],
            [
                {
                    "type": "tts_say",
                    "text": (
                        "Please Google a local sign company. Billboard Source helps "
                        "clients find billboards to advertise on. Thanks for calling, "
                        "goodbye."
                    ),
                },
                {"type": "end_conversation"},
            ],
        )
        self.assertNotIn("post_actions", nodes[1])
        self.assertEqual(
            nodes[4]["pre_actions"][-1],
            {"type": "end_conversation"},
        )
        self.assertNotIn("post_actions", nodes[4])

        callback_number_node = create_callback_number_node()
        self.assertEqual(
            callback_number_node["pre_actions"],
            [
                {
                    "type": "tts_say",
                    "text": "What is your best callback phone number?",
                }
            ],
        )
        self.assertIs(callback_number_node.get("respond_immediately"), False)

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

    def test_initial_node_asks_property_build_question(self) -> None:
        initial_node = create_initial_node()

        self.assertEqual(
            initial_node.get("pre_actions"),
            [
                {
                    "type": "tts_say",
                    "text": (
                        "Thanks for calling Billboard Source, how can I help you? "
                        "Are you looking to build a billboard on your property or "
                        "advertise your business?"
                    ),
                }
            ],
        )
        self.assertIs(initial_node.get("respond_immediately"), False)

    def test_property_inquiry_routes_directly_to_sign_company_guidance(self) -> None:
        flow_manager = cast(FlowManager, SimpleNamespace(state={}))

        result, next_node = asyncio.run(
            collect_inquiry_type(flow_manager, "property question")
        )

        self.assertEqual(result["value"], "property")
        self.assertEqual(next_node.get("name"), "property_end")
        self.assertEqual(next_node.get("task_messages"), [])
        self.assertEqual(
            next_node.get("pre_actions"),
            [
                {
                    "type": "tts_say",
                    "text": (
                        "Please Google a local sign company. Billboard Source helps "
                        "clients find billboards to advertise on. Thanks for calling, "
                        "goodbye."
                    ),
                },
                {"type": "end_conversation"},
            ],
        )
        self.assertFalse(next_node.get("respond_immediately"))

    def test_advertising_inquiry_requests_all_lead_information(self) -> None:
        flow_manager = cast(FlowManager, SimpleNamespace(state={}))

        _, lead_node = asyncio.run(
            collect_inquiry_type(flow_manager, "advertising")
        )

        self.assertEqual(lead_node.get("name"), "business_lead")
        self.assertEqual(len(lead_node.get("pre_actions", [])), 1)
        request = lead_node["pre_actions"][0]
        self.assertEqual(request.get("type"), "tts_say")
        for field in (
            "full name",
            "business information",
            "contact info",
        ):
            self.assertIn(field, request.get("text", ""))
        self.assertNotIn("location", request.get("text", ""))
        self.assertNotIn("callback number", request.get("text", ""))
        self.assertIs(lead_node.get("respond_immediately"), False)
        prompt = lead_node["task_messages"][0]["content"]
        self.assertNotIn("location", prompt)
        self.assertIn("otherwise omit phone", prompt)
        self.assertIn("without repeating or confirming", prompt)

    def test_negated_property_description_routes_to_advertising(self) -> None:
        flow_manager = cast(FlowManager, SimpleNamespace(state={}))

        result, next_node = asyncio.run(
            collect_inquiry_type(
                flow_manager,
                cast(
                    Any,
                    "advertising, not building a billboard on my property",
                ),
            )
        )

        self.assertEqual(result["value"], "advertising")
        self.assertEqual(next_node.get("name"), "business_lead")

    def test_missing_phone_confirms_incoming_number_after_business_information(self) -> None:
        flow_manager = cast(
            FlowManager,
            SimpleNamespace(state={"calling_phone": "+15551234567"}),
        )
        create_lead = AsyncMock()

        with patch(
            "src.flows.voicemail.submit_nutshell_lead",
            new=create_lead,
        ):
            _, next_node = asyncio.run(
                collect_business_lead(
                    flow_manager,
                    full_name="Jane Smith",
                    business_name="Example Company",
                    email="jane@example.com",
                )
            )

        create_lead.assert_not_awaited()
        self.assertEqual(
            flow_manager.state["pending_callback_phone"],
            "+15551234567",
        )
        self.assertEqual(next_node.get("name"), "confirm_callback_number")
        self.assertEqual(
            next_node["pre_actions"][0]["text"],
            "Is the number you are calling from a good callback number?",
        )

    def test_this_number_resolves_to_incoming_number_before_confirmation(self) -> None:
        flow_manager = cast(
            FlowManager,
            SimpleNamespace(state={"calling_phone": "+15551234567"}),
        )
        create_lead = AsyncMock()

        with patch(
            "src.flows.voicemail.submit_nutshell_lead",
            new=create_lead,
        ):
            _, next_node = asyncio.run(
                collect_business_lead(
                    flow_manager,
                    full_name="Jane Smith",
                    business_name="Example Company",
                    email="jane@example.com",
                    phone="this number",
                )
            )

        create_lead.assert_not_awaited()
        self.assertEqual(
            flow_manager.state["pending_callback_phone"],
            "+15551234567",
        )
        self.assertEqual(next_node.get("name"), "confirm_callback_number")
        self.assertEqual(
            next_node["pre_actions"][0]["text"],
            "Is the number you are calling from a good callback number?",
        )

    def test_missing_business_name_is_requested_before_phone_confirmation(self) -> None:
        flow_manager = cast(
            FlowManager,
            SimpleNamespace(state={"calling_phone": "+15551234567"}),
        )
        create_lead = AsyncMock()

        with patch(
            "src.flows.voicemail.submit_nutshell_lead",
            new=create_lead,
        ):
            _, next_node = asyncio.run(
                collect_business_lead(
                    flow_manager,
                    full_name="Jane Smith",
                    business_name="Not provided",
                    email="jane@example.com",
                    phone="this number",
                )
            )

        create_lead.assert_not_awaited()
        self.assertEqual(flow_manager.state["name"], "Jane Smith")
        self.assertEqual(flow_manager.state["email"], "jane@example.com")
        self.assertEqual(
            flow_manager.state["pending_callback_phone"],
            "+15551234567",
        )
        self.assertEqual(next_node.get("name"), "business_lead")
        self.assertEqual(
            next_node["pre_actions"],
            [
                {
                    "type": "tts_say",
                    "text": "What is the name of your business?",
                }
            ],
        )
        prompt = next_node["task_messages"][0]["content"]
        self.assertIn("previously provided full name", prompt)
        self.assertIn("business name", prompt)

    def test_confirmed_incoming_number_creates_lead(self) -> None:
        flow_manager = cast(
            FlowManager,
            SimpleNamespace(
                state={
                    "calling_phone": "+15551234567",
                    "pending_callback_phone": "+15551234567",
                    "name": "Jane Smith",
                    "business_name": "Example Company",
                    "email": "jane@example.com",
                }
            ),
        )
        create_lead = AsyncMock(return_value={"id": "6-leads"})

        with patch(
            "src.flows.voicemail.submit_nutshell_lead",
            new=create_lead,
        ):
            _, next_node = asyncio.run(
                confirm_callback_number(flow_manager, True)
            )

        self.assertEqual(flow_manager.state["phone"], "+15551234567")
        create_lead.assert_awaited_once_with({}, flow_manager)
        self.assertEqual(next_node.get("name"), "associate_followup")

    def test_supplied_phone_is_confirmed_before_lead_creation(self) -> None:
        flow_manager = cast(FlowManager, SimpleNamespace(state={}))
        create_lead = AsyncMock()

        with patch(
            "src.flows.voicemail.submit_nutshell_lead",
            new=create_lead,
        ):
            _, next_node = asyncio.run(
                collect_business_lead(
                    flow_manager,
                    full_name="Jane Smith",
                    business_name="Example Company",
                    email="jane@example.com",
                    phone="+15551234567",
                )
            )

        self.assertEqual(flow_manager.state["name"], "Jane Smith")
        self.assertEqual(flow_manager.state["business_name"], "Example Company")
        self.assertNotIn("billboard_location", flow_manager.state)
        self.assertEqual(flow_manager.state["email"], "jane@example.com")
        self.assertEqual(
            flow_manager.state["pending_callback_phone"],
            "+15551234567",
        )
        self.assertNotIn("phone", flow_manager.state)
        self.assertNotIn("call_summary", flow_manager.state)
        create_lead.assert_not_awaited()
        self.assertEqual(next_node.get("name"), "confirm_callback_number")
        self.assertEqual(
            next_node["pre_actions"],
            [
                {
                    "type": "tts_say",
                    "text": "Is +15551234567 the correct callback number?",
                },
            ],
        )

    def test_collected_replacement_phone_is_confirmed_before_lead_creation(self) -> None:
        flow_manager = cast(
            FlowManager,
            SimpleNamespace(
                state={
                    "calling_phone": "+15550000000",
                    "name": "Jane Smith",
                    "business_name": "Example Company",
                    "email": "jane@example.com",
                }
            ),
        )
        create_lead = AsyncMock()

        with patch(
            "src.flows.voicemail.submit_nutshell_lead",
            new=create_lead,
        ):
            _, next_node = asyncio.run(
                collect_callback_number(flow_manager, "+15551234567")
            )

        create_lead.assert_not_awaited()
        self.assertEqual(
            flow_manager.state["pending_callback_phone"],
            "+15551234567",
        )
        self.assertEqual(next_node.get("name"), "confirm_callback_number")
        self.assertEqual(
            next_node["pre_actions"][0]["text"],
            "Is +15551234567 the correct callback number?",
        )

    def test_confirmation_submits_staged_phone_instead_of_incoming_phone(self) -> None:
        flow_manager = cast(
            FlowManager,
            SimpleNamespace(
                state={
                    "calling_phone": "+15550000000",
                    "pending_callback_phone": "+15551234567",
                    "name": "Jane Smith",
                    "business_name": "Example Company",
                    "email": "jane@example.com",
                }
            ),
        )
        create_lead = AsyncMock(return_value={"id": "6-leads"})

        with patch(
            "src.flows.voicemail.submit_nutshell_lead",
            new=create_lead,
        ):
            _, next_node = asyncio.run(
                confirm_callback_number(flow_manager, True)
            )

        self.assertEqual(flow_manager.state["phone"], "+15551234567")
        self.assertEqual(
            flow_manager.state["call_summary"],
            "Jane Smith from Example Company wants billboard advertising. "
            "Contact: jane@example.com, +15551234567. "
            "Twilio caller number: +15550000000.",
        )
        create_lead.assert_awaited_once_with({}, flow_manager)
        self.assertEqual(next_node.get("name"), "associate_followup")

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
        self.assertIsNone(lead.billboard_location)
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

    def test_nutshell_action_skips_lead_when_submission_is_disabled(self) -> None:
        flow_manager = cast(
            FlowManager,
            SimpleNamespace(
                state={
                    "nutshell_submission_enabled": False,
                    "phone": "+15551234567",
                }
            ),
        )
        create_lead = AsyncMock(return_value={"id": "6-leads"})

        with patch(
            "src.flows.tools.create_nutshell_lead",
            new=create_lead,
        ):
            result = asyncio.run(submit_nutshell_lead({}, flow_manager))

        self.assertIsNone(result)
        create_lead.assert_not_awaited()

    def test_nutshell_action_honors_disabled_environment_flag(self) -> None:
        flow_manager = cast(
            FlowManager,
            SimpleNamespace(state={"phone": "+15551234567"}),
        )
        create_lead = AsyncMock(return_value={"id": "6-leads"})

        with (
            patch.dict(
                os.environ,
                {"NUTSHELL_LEAD_SUBMISSION_ENABLED": "false"},
            ),
            patch(
                "src.flows.tools.create_nutshell_lead",
                new=create_lead,
            ),
        ):
            result = asyncio.run(submit_nutshell_lead({}, flow_manager))

        self.assertIsNone(result)
        create_lead.assert_not_awaited()

    def test_nutshell_action_skips_lead_without_caller_details(self) -> None:
        flow_manager = cast(
            FlowManager,
            SimpleNamespace(state={"billboard_location": "Denver, CO"}),
        )
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
