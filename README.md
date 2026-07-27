# billboard-source-voicemail-ai
python app using pipecat to create a voicemail AI system

## Twilio TaskRouter caller information

When the voicemail worker connects, its Twilio call SID is used to find the
matching TaskRouter task in the `Billboard Source Sales` workspace. Caller and
dialed numbers are read from the task's `from` and `to` attributes.

The workspace SID defaults to the configured production workspace and can be
overridden with `TWILIO_TASKROUTER_WORKSPACE_SID`.

## Neon billboard locations

Set the project-scoped Neon API key in your environment:

```dotenv
NEON_API_KEY=your-neon-api-key
```

`get_billboard_locations()` uses the key to retrieve a pooled connection URI for
the `billboardsourceAI-prod-2` project's production `neondb` database, then reads
the `billboard_locations` table. A `DATABASE_URL`, when set, takes precedence and
avoids the extra Neon API request.

## Nutshell leads

Set the Nutshell user's email address and API key in your environment:

```dotenv
NUTSHELL_EMAIL=user@example.com
NUTSHELL_API_KEY=your-api-key
```

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

Each lead has a one-in-three chance of being assigned to Ashton Pruitt. The
remaining leads are assigned to the user configured by `NUTSHELL_EMAIL`.
