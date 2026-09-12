import unittest

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from twilio.request_validator import RequestValidator

from main import _uses_twilio
from twilio_signature import (
    TwilioSignatureMiddleware,
    validate_twilio_config,
)

AUTH_TOKEN = "test-auth-token"
PUBLIC_HOST = "voicemail-agent.john22n-iii.com"


def signed_app() -> TestClient:
    app = FastAPI()

    @app.post("/")
    async def webhook():
        return {"accepted": True}

    @app.websocket("/ws")
    async def websocket(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("bot-started")
        await websocket.close()

    app.add_middleware(
        TwilioSignatureMiddleware,
        auth_token=AUTH_TOKEN,
        public_host=PUBLIC_HOST,
    )
    return TestClient(app, base_url="http://docker-internal:7860")


class TwilioSignatureTests(unittest.TestCase):
    def test_signed_webhook_is_accepted_using_public_proxy_url(self) -> None:
        params = {"CallSid": "CA123", "From": "+15551234567"}
        signature = RequestValidator(AUTH_TOKEN).compute_signature(
            f"https://{PUBLIC_HOST}/", params
        )

        response = signed_app().post(
            "/", data=params, headers={"X-Twilio-Signature": signature}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"accepted": True})

    def test_unsigned_webhook_is_forbidden(self) -> None:
        response = signed_app().post("/", data={"CallSid": "CA123"})

        self.assertEqual(response.status_code, 403)

    def test_tampered_webhook_is_forbidden(self) -> None:
        signature = RequestValidator(AUTH_TOKEN).compute_signature(
            f"https://{PUBLIC_HOST}/", {"CallSid": "CA123"}
        )

        response = signed_app().post(
            "/",
            data={"CallSid": "CA-tampered"},
            headers={"X-Twilio-Signature": signature},
        )

        self.assertEqual(response.status_code, 403)

    def test_signed_websocket_handshake_is_accepted(self) -> None:
        signature = RequestValidator(AUTH_TOKEN).compute_signature(
            f"https://{PUBLIC_HOST}/ws", {}
        )

        with signed_app().websocket_connect(
            "/ws", headers={"X-Twilio-Signature": signature}
        ) as websocket:
            self.assertEqual(websocket.receive_text(), "bot-started")

    def test_unsigned_websocket_handshake_is_rejected(self) -> None:
        with (
            self.assertRaises(WebSocketDisconnect),
            signed_app().websocket_connect("/ws"),
        ):
            self.fail("unsigned WebSocket unexpectedly connected")

    def test_tampered_websocket_url_is_rejected(self) -> None:
        signature = RequestValidator(AUTH_TOKEN).compute_signature(
            f"https://{PUBLIC_HOST}/ws?call=original", {}
        )

        with (
            self.assertRaises(WebSocketDisconnect),
            signed_app().websocket_connect(
                "/ws?call=tampered", headers={"X-Twilio-Signature": signature}
            ),
        ):
            self.fail("tampered WebSocket unexpectedly connected")

    def test_twilio_configuration_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "TWILIO_AUTH_TOKEN"):
            validate_twilio_config("", PUBLIC_HOST)
        with self.assertRaisesRegex(ValueError, "PUBLIC_HOST"):
            validate_twilio_config(AUTH_TOKEN, "")

    def test_only_twilio_runner_arguments_enable_validation(self) -> None:
        self.assertTrue(_uses_twilio(["--transport", "twilio"]))
        self.assertTrue(_uses_twilio(["-t", "twilio"]))
        self.assertTrue(_uses_twilio(["--transport=twilio"]))
        self.assertFalse(_uses_twilio(["--transport", "webrtc"]))


if __name__ == "__main__":
    unittest.main()
