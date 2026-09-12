import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from xml.etree import ElementTree

from call_limits import CallLimitsMiddleware
from twilio_signature import TwilioSignatureMiddleware


class CallLimitsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = AsyncMock()
        self.limits = CallLimitsMiddleware(
            self.bot, public_host="voice.example.com", max_calls=1
        )

    async def request(self, path="/", kind="http", query=b"", app=None):
        send = AsyncMock()
        await (app or self.limits)(
            {
                "type": kind,
                "method": "POST",
                "path": path,
                "raw_path": path.encode(),
                "query_string": query,
                "headers": [],
            },
            AsyncMock(return_value={"type": "http.request", "body": b""}),
            send,
        )
        return [call.args[0] for call in send.call_args_list]

    async def reserve(self):
        messages = await self.request()
        xml = ElementTree.fromstring(messages[-1]["body"])
        return (
            xml.find("Connect/Stream")
            .attrib["url"]
            .replace("wss://voice.example.com", "")
        )

    async def test_pending_capacity_falls_back_and_recording_callback_hangs_up(self):
        await self.reserve()
        messages = await self.request()
        xml = ElementTree.fromstring(messages[-1]["body"])
        self.assertIsNone(xml.find("Connect"))
        self.assertEqual(xml.find("Record").attrib["maxLength"], "120")
        self.assertEqual(
            xml.find("Record").attrib["action"], "https://voice.example.com/?recorded=1"
        )
        messages = await self.request(query=b"recorded=1")
        xml = ElementTree.fromstring(messages[-1]["body"])
        self.assertIsNotNone(xml.find("Hangup"))
        self.assertIsNone(xml.find("Record"))

    async def test_disconnect_releases_slot_and_token_cannot_be_reused(self):
        path = await self.reserve()
        await self.request(path, "websocket")
        self.bot.assert_awaited_once()
        self.assertEqual(self.limits.active, set())
        messages = await self.request(path, "websocket")
        self.assertEqual(messages, [{"type": "websocket.close", "code": 1008}])
        self.assertNotEqual(await self.reserve(), path)

    async def test_active_call_blocks_admission_and_timeout_releases_slot(self):
        started = asyncio.Event()

        async def running(*args):
            started.set()
            await asyncio.Event().wait()

        self.bot.side_effect = running
        self.limits.max_seconds = 0.05
        path = await self.reserve()
        task = asyncio.create_task(self.request(path, "websocket"))
        await started.wait()
        messages = await self.request()
        self.assertIn(b"<Record", messages[-1]["body"])
        messages = await task
        self.assertEqual(messages[-1], {"type": "websocket.close", "code": 1000})
        self.assertFalse(self.limits.active)
        await self.reserve()

    async def test_expired_reservations_and_direct_socket_are_rejected(self):
        with patch("call_limits.time.monotonic", return_value=100):
            path = await self.reserve()
        with patch("call_limits.time.monotonic", return_value=130):
            messages = await self.request(path, "websocket")
            self.assertEqual(messages[0]["code"], 1008)
            await self.reserve()
        messages = await self.request("/ws", "websocket")
        self.assertEqual(messages[0]["code"], 1008)
        self.bot.assert_not_awaited()

    async def test_unsigned_request_cannot_reserve_capacity(self):
        secured = TwilioSignatureMiddleware(
            self.limits, auth_token="test", public_host="voice.example.com"
        )
        messages = await self.request(app=secured)
        self.assertEqual(messages[0]["status"], 403)
        self.assertFalse(self.limits.pending)

    async def test_error_releases_capacity(self):
        path = await self.reserve()
        self.bot.side_effect = RuntimeError("failed")
        with self.assertRaises(RuntimeError):
            await self.request(path, "websocket")
        self.assertFalse(self.limits.active)
        await self.reserve()
