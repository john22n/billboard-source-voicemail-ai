"""Single-process admission control for authenticated Twilio calls."""

import asyncio
import os
import secrets
import time
from urllib.parse import parse_qs

from starlette.responses import Response
from twilio.twiml.voice_response import Connect, VoiceResponse


class CallLimitsMiddleware:
    def __init__(
        self,
        app,
        *,
        public_host: str,
        max_calls: int = 2,
        max_seconds: int = 300,
        recording_seconds: int = 120,
    ):
        if max_calls < 1 or max_seconds < 1 or not 1 <= recording_seconds <= 3600:
            raise ValueError(
                "Call limits must be positive; recording limit must be 1–3600"
            )
        self.app = app
        self.public_host = public_host
        self.max_calls = max_calls
        self.max_seconds = max_seconds
        self.recording_seconds = recording_seconds
        self.pending = {}
        self.active = set()

    async def __call__(self, scope, receive, send):
        path = scope.get("path")
        query = parse_qs(scope.get("query_string", b"").decode())
        if scope["type"] == "http" and scope.get("method") == "POST" and path == "/":
            response = VoiceResponse()
            if query.get("recorded") == ["1"]:
                response.say("Thank you for your message. Goodbye.")
                response.hangup()
            else:
                now = time.monotonic()
                self.pending = {
                    key: expiry for key, expiry in self.pending.items() if expiry > now
                }
                if len(self.pending) + len(self.active) >= self.max_calls:
                    response.say(
                        "Our assistants are busy. Please leave your name, phone number, and message after the beep."
                    )
                    response.record(
                        max_length=self.recording_seconds,
                        timeout=5,
                        action=f"https://{self.public_host}/?recorded=1",
                        method="POST",
                        play_beep=True,
                    )
                    response.hangup()
                else:
                    token = secrets.token_urlsafe(32)
                    # No await between checking capacity and reserving a slot.
                    self.pending[token] = now + 30
                    connect = Connect()
                    connect.stream(url=f"wss://{self.public_host}/ws/{token}")
                    response.append(connect)
                    response.say("Your session has ended. Goodbye.")
                    response.hangup()
            await Response(str(response), media_type="application/xml")(
                scope, receive, send
            )
            return

        if scope["type"] == "websocket" and (path == "/ws" or path.startswith("/ws/")):
            token = path.removeprefix("/ws/")
            expiry = self.pending.pop(token, 0)
            if expiry <= time.monotonic():
                await send({"type": "websocket.close", "code": 1008})
                return
            self.active.add(token)
            closed = False

            async def guarded_send(message):
                nonlocal closed
                if closed:
                    return
                if message["type"] == "websocket.close":
                    closed = True
                await send(message)

            session = asyncio.create_task(self.app(scope, receive, guarded_send))
            try:
                done, _ = await asyncio.wait({session}, timeout=self.max_seconds)
                if done:
                    await session
                else:
                    # Close audio before cancellation cleanup (which may submit
                    # a lead over the network) so it cannot extend the call.
                    await guarded_send({"type": "websocket.close", "code": 1000})
            finally:
                session.cancel()
                try:
                    await asyncio.gather(session, return_exceptions=True)
                finally:
                    self.active.discard(token)
            return
        await self.app(scope, receive, send)


def configure_call_limits(app, public_host):
    app.add_middleware(
        CallLimitsMiddleware,
        public_host=public_host,
        max_calls=int(os.getenv("MAX_CONCURRENT_CALLS", "2")),
        max_seconds=int(os.getenv("MAX_CALL_DURATION_SECONDS", "300")),
        recording_seconds=int(os.getenv("VOICEMAIL_RECORDING_SECONDS", "120")),
    )
