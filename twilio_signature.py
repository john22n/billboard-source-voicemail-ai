"""Twilio request authentication for the Pipecat development runner."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import parse_qsl

from multidict import MultiDict
from twilio.request_validator import RequestValidator


def validate_twilio_config(auth_token: str, public_host: str) -> None:
    """Reject a Twilio server configuration that cannot authenticate requests."""
    if not auth_token:
        raise ValueError("TWILIO_AUTH_TOKEN must be set for the Twilio transport")
    if not public_host or "://" in public_host or public_host.endswith("/"):
        raise ValueError(
            "PUBLIC_HOST must be a hostname without a protocol or trailing slash"
        )


class TwilioSignatureMiddleware:
    """Validate Twilio's webhook and Media Streams handshake signatures."""

    def __init__(self, app, *, auth_token: str, public_host: str) -> None:
        validate_twilio_config(auth_token, public_host)

        self.app = app
        self.validator = RequestValidator(auth_token)
        self.public_host = public_host

    async def __call__(self, scope, receive, send) -> None:
        request_type = scope["type"]
        path = scope.get("path", "")
        if request_type == "http" and scope.get("method") == "POST" and path == "/":
            await self._validate_webhook(scope, receive, send)
            return
        if (
            request_type == "websocket"
            and path in {"/ws", "/ws/"}
            and not self._is_valid(scope, {})
        ):
            await send({"type": "websocket.close", "code": 1008})
            return
        await self.app(scope, receive, send)

    async def _validate_webhook(self, scope, receive, send) -> None:
        messages = []
        body = bytearray()
        while True:
            message = await receive()
            messages.append(message)
            body.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break

        params = MultiDict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))
        if not self._is_valid(scope, params):
            await send(
                {
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [(b"content-type", b"text/plain; charset=utf-8")],
                }
            )
            await send({"type": "http.response.body", "body": b"Forbidden"})
            return

        async def replay_receive():
            if messages:
                return messages.pop(0)
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)

    def _is_valid(self, scope, params) -> bool:
        signature = dict(self._headers(scope)).get(b"x-twilio-signature", b"")
        if not signature:
            return False
        return self.validator.validate(
            self._canonical_url(scope),
            params,
            signature.decode("latin-1"),
        )

    def _canonical_url(self, scope) -> str:
        # TLS terminates at Nginx, so internal request metadata cannot provide the
        # URL Twilio signed. WebSocket handshakes are signed as HTTPS requests.
        url = f"https://{self.public_host}{scope.get('raw_path', b'/').decode('ascii')}"
        query = scope.get("query_string", b"")
        if query:
            url += f"?{query.decode('ascii')}"
        return url

    @staticmethod
    def _headers(scope) -> Iterable[tuple[bytes, bytes]]:
        return ((name.lower(), value) for name, value in scope.get("headers", []))
