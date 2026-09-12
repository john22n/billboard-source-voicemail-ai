import argparse
import os
import sys


def _runner_options(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("-t", "--transport")
    parser.add_argument("-x", "--proxy")
    return parser.parse_known_args(argv)[0]


if __name__ == "__main__":
    from pipecat.runner.run import app, main

    options = _runner_options(sys.argv[1:])
    if options.transport == "twilio":
        from twilio_signature import TwilioSignatureMiddleware, validate_twilio_config

        auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        # Match the hostname used in Pipecat's generated TwiML. CLI wins over
        # .env, particularly when dev-twilio.sh supplies a fresh ngrok hostname.
        public_host = options.proxy or os.environ.get("PUBLIC_HOST", "")
        validate_twilio_config(auth_token, public_host)

        from call_limits import configure_call_limits

        # Starlette runs the last-added middleware first: authenticate before
        # reserving capacity or returning recording instructions.
        configure_call_limits(app, public_host)
        app.add_middleware(
            TwilioSignatureMiddleware,
            auth_token=auth_token,
            public_host=public_host,
        )

    main()
