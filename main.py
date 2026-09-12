import os
import sys


def _uses_twilio(argv: list[str]) -> bool:
    return any(
        argument == "twilio"
        and index > 0
        and argv[index - 1] in {"-t", "--transport"}
        for index, argument in enumerate(argv)
    ) or any(argument == "--transport=twilio" for argument in argv)


if __name__ == "__main__":
    from pipecat.runner.run import app, main

    if _uses_twilio(sys.argv[1:]):
        from twilio_signature import TwilioSignatureMiddleware, validate_twilio_config

        auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        public_host = os.environ.get("PUBLIC_HOST", "")
        validate_twilio_config(auth_token, public_host)

        app.add_middleware(
            TwilioSignatureMiddleware,
            auth_token=auth_token,
            public_host=public_host,
        )

    main()
