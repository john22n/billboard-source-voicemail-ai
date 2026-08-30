# billboard-source-voicemail-ai
python app using pipecat to create a voicemail AI system

## Twilio caller information

When the voicemail worker connects, its Twilio call SID is used to retrieve the
call from Twilio's Calls API. Caller and dialed numbers are read directly from
the call's `from` and `to` fields.

Set `TWILIO_ACCOUNT_SID` and `TWILIO_AUTH_TOKEN` in your local `.env` file. No
Twilio resource identifiers or credentials are stored in the repository.

### Run Twilio locally with ngrok

Install and authenticate the ngrok CLI once, then start both Pipecat and ngrok
with one command:

```bash
./dev-twilio.sh
```

The script discovers the generated HTTPS tunnel, passes its hostname to the
Pipecat Twilio runner, and prints the public voice webhook URL. Set that URL as
the Twilio phone number's incoming-call webhook. Press Ctrl+C to stop both
processes.

Pipecat uses port `7860` by default. Override it or pass additional Pipecat
runner arguments when needed:

```bash
PORT=8000 ./dev-twilio.sh --verbose
```

## Neon billboard locations

Set the project-scoped Neon API key in your environment:

```dotenv
NEON_API_KEY=your-neon-api-key
NEON_PROJECT_ID=your-neon-project-id
```

`get_billboard_locations()` uses the key to retrieve a pooled connection URI for
the configured project's `neondb` database, then reads the
`billboard_locations` table. A `DATABASE_URL`, when set, takes precedence and
avoids the extra Neon API request. Keep all real values in `.env`, which is
ignored by Git; use `.env.example` as the safe configuration template.

## Nutshell leads

Set the Nutshell user's email address, API key, and production submission flag in
the deployed environment:

```dotenv
NUTSHELL_EMAIL=user@example.com
NUTSHELL_API_KEY=your-api-key
NUTSHELL_LEAD_SUBMISSION_ENABLED=true
```

Local eval and WebRTC sessions never submit leads. `dev-eval.sh` and
`dev-twilio.sh` also disable submission, and `.env.example` defaults the flag to
`false`, so testing locally cannot write test leads to Nutshell. When submission
is disabled, the call still completes normally with a generic associate
follow-up message.

Create one lead through the Nutshell REST API:

```python
from src.data_source.nutshell import create_nutshell_lead
from src.models.sales_call_lead import LeadInformation

lead = await create_nutshell_lead(
    LeadInformation(
        name="Jane Smith",
        email="jane@example.com",
        phone="+15551234567",
        business="Example Company",
        billboard_location="Detroit, MI",
        notes="Interested in a digital billboard",
    )
)
```

Voice leads are created without an owner and placed in the `NEW BSI Pipeline`, so
Nutshell's user-assignment rules select the owner. Configure that pipeline's first
stage with a team and enable **Round-robin**. The agent reads the assigned user's
first name and email from Nutshell, tells the caller who will follow up, and ends
the call. It does not transfer or hand off the call.
