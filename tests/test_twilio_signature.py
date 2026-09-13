import unittest
from urllib.parse import urlsplit
from xml.etree import ElementTree

from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from twilio.request_validator import RequestValidator

from call_limits import CallLimitsMiddleware
from main import _runner_options
from twilio_signature import (
    TwilioSignatureMiddleware,
    validate_twilio_config,
)

AUTH_TOKEN = "test-auth-token"
PUBLIC_HOST = "voicemail-agent.john22n-iii.com"


def signed_app(*, with_limits=False) -> TestClient:
    app = FastAPI()

    @app.post("/")
    async def webhook(request: Request):
        return {"accepted": True, "params": dict(await request.form())}

    @app.websocket("/ws/{token}")
    @app.websocket("/ws")
    async def websocket(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("bot-started")
        await websocket.close()

    if with_limits:
        app.add_middleware(CallLimitsMiddleware, public_host=PUBLIC_HOST)
    app.add_middleware(
        TwilioSignatureMiddleware,
        auth_token=AUTH_TOKEN,
        public_host=PUBLIC_HOST,
    )
    return TestClient(app, base_url="http://docker-internal:7860")


class TwilioSignatureTests(unittest.TestCase):
    def test_generated_stream_url_can_authenticate_and_claim_its_reservation(self):
        client = signed_app(with_limits=True)
        validator = RequestValidator(AUTH_TOKEN)
        response = client.post(
            "/",
            data={},
            headers={
                "X-Twilio-Signature": validator.compute_signature(
                    f"https://{PUBLIC_HOST}/", {}
                )
            },
        )
        stream_url = (
            ElementTree.fromstring(response.text).find("Connect/Stream").attrib["url"]
        )
        self.assertTrue(stream_url.startswith(f"wss://{PUBLIC_HOST}/ws/"))
        signature = validator.compute_signature(stream_url, {})
        with client.websocket_connect(
            urlsplit(stream_url).path, headers={"X-Twilio-Signature": signature}
        ) as websocket:
            self.assertEqual(websocket.receive_text(), "bot-started")

    def test_wss_variants_preserve_host_path_and_query_validation(self):
        for slash in ("", "/"):
            signature = RequestValidator(AUTH_TOKEN).compute_signature(
                f"wss://{PUBLIC_HOST}/ws/token{slash}?call=original", {}
            )
            with signed_app().websocket_connect(
                "/ws/token?call=original", headers={"X-Twilio-Signature": signature}
            ) as websocket:
                self.assertEqual(websocket.receive_text(), "bot-started")
            for path in ("/ws/other?call=original", "/ws/token?call=tampered"):
                with (
                    self.assertRaises(WebSocketDisconnect),
                    signed_app().websocket_connect(
                        path, headers={"X-Twilio-Signature": signature}
                    ),
                ):
                    self.fail("tampered WebSocket accepted")

    def test_signed_webhook_is_accepted_using_public_proxy_url(self) -> None:
        params = {"CallSid": "CA123", "From": "+15551234567"}
        signature = RequestValidator(AUTH_TOKEN).compute_signature(
            f"https://{PUBLIC_HOST}/", params
        )

        response = signed_app().post(
            "/", data=params, headers={"X-Twilio-Signature": signature}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"accepted": True, "params": params})

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
        for path in ["/ws", "/ws/token"]:
            with (
                self.subTest(path=path),
                self.assertRaises(WebSocketDisconnect),
                signed_app().websocket_connect(path),
            ):
                self.fail("unsigned WebSocket unexpectedly connected")

    def test_twilio_trailing_slash_signature_on_tokenized_handshake(self) -> None:
        signature = RequestValidator(AUTH_TOKEN).compute_signature(
            f"https://{PUBLIC_HOST}/ws/reservation-token/", {}
        )
        with signed_app().websocket_connect(
            "/ws/reservation-token", headers={"X-Twilio-Signature": signature}
        ) as websocket:
            self.assertEqual(websocket.receive_text(), "bot-started")

        with (
            self.assertRaises(WebSocketDisconnect),
            signed_app().websocket_connect(
                "/ws/another-token", headers={"X-Twilio-Signature": signature}
            ),
        ):
            self.fail("signature for another reservation was accepted")

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
        for args in [
            ["--transport", "twilio"],
            ["-t", "twilio"],
            ["--transport=twilio"],
        ]:
            self.assertEqual(_runner_options(args).transport, "twilio")
        self.assertEqual(_runner_options(["--transport", "webrtc"]).transport, "webrtc")

    def test_proxy_uses_same_cli_option_as_runner(self) -> None:
        options = _runner_options(
            ["--transport", "twilio", "--proxy", "fresh.ngrok.app", "--port", "7860"]
        )
        self.assertEqual(options.proxy, "fresh.ngrok.app")
        self.assertEqual(
            _runner_options(["-x", "short.ngrok.app"]).proxy, "short.ngrok.app"
        )


if __name__ == "__main__":
    unittest.main()
