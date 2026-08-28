# Voicemail flow evals

Run the exhaustive suite from the repository root with:

```bash
uv run dotenv -f .env run -- uv run pipecat eval suite scenarios/suite.yml
```

The suite starts a fresh bot for each scenario and covers:

- the spoken greeting;
- direct advertising, affirmative property, and negated-property routing;
- complete and incremental lead-information collection;
- a callback number supplied with the lead;
- callback collection when no incoming number is available;
- acceptance and replacement of a simulated incoming number; and
- the assigned associate announcement and call ending.

Run one scenario against a locally started eval transport with:

```bash
SCENARIO="$PWD/scenarios/property_inquiry.yml" ./dev-eval.sh --verbose
```

The incoming-number scenarios also require the eval runner body:

```bash
SCENARIO="$PWD/scenarios/advertising_incoming_callback_accepted.yml" \
RUNNER_BODY="$PWD/scenarios/incoming_call.json" \
./dev-eval.sh --verbose
```

The four lead-completion scenarios use the configured Nutshell account and each
create a real lead in `NEW BSI Pipeline`. This is required to exercise Nutshell's
actual round-robin rule and verify that the agent announces the assigned
associate's name and email address before ending the call. The routing-only
scenarios do not create leads.
