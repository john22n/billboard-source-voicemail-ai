# Voicemail flow evals

Run a scenario against a locally started eval transport with:

```bash
SCENARIO="$PWD/scenarios/property_inquiry.yml" ./dev-eval.sh --verbose
```

Scenarios:

- `inquire_question.yml` verifies routing after the initial property question.
- `property_inquiry.yml` verifies property routing to local sign-company guidance.
- `advertising_lead_assignment.yml` verifies the complete lead-capture flow and
  Nutshell round-robin assignment.

`advertising_lead_assignment.yml` uses the configured Nutshell account and creates
a real lead named **Billboard Source Voice Eval** in `NEW BSI Pipeline`. This is
required to exercise Nutshell's actual round-robin rule and verify that the agent
announces the assigned associate's name and email address before ending the call.
