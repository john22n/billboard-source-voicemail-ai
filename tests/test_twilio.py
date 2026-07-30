import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from src.data_source.twilio import get_call_info


class TwilioTests(unittest.TestCase):
    def test_call_sid_is_required(self) -> None:
        self.assertIsNone(asyncio.run(get_call_info(None)))

    def test_credentials_are_required(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(asyncio.run(get_call_info("CA123")))

    def test_get_call_info_returns_phone_numbers(self) -> None:
        response = MagicMock(status=200)
        response.json = AsyncMock(
            return_value={
                "sid": "CA123",
                "from": "+15551234567",
                "to": "+15557654321",
            }
        )
        response_context = AsyncMock()
        response_context.__aenter__.return_value = response

        session = MagicMock()
        session.get.return_value = response_context
        session_context = AsyncMock()
        session_context.__aenter__.return_value = session

        with (
            patch.dict(
                os.environ,
                {
                    "TWILIO_ACCOUNT_SID": "AC123",
                    "TWILIO_AUTH_TOKEN": "secret",
                },
                clear=True,
            ),
            patch(
                "src.data_source.twilio.aiohttp.ClientSession",
                return_value=session_context,
            ),
        ):
            call_info = asyncio.run(get_call_info("CA123"))

        self.assertIsNotNone(call_info)
        self.assertEqual(call_info.from_number, "+15551234567")
        self.assertEqual(call_info.to_number, "+15557654321")
        session.get.assert_called_once()
        request_url = session.get.call_args.args[0]
        self.assertEqual(
            request_url,
            "https://api.twilio.com/2010-04-01/Accounts/AC123/Calls/CA123.json",
        )
        self.assertEqual(
            session.get.call_args.kwargs["headers"],
            {"Authorization": "Basic QUMxMjM6c2VjcmV0"},
        )


if __name__ == "__main__":
    unittest.main()
