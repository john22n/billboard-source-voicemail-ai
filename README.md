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

## Deploy to a VPS with Docker and Jaeger

This repository includes a deployable app image and a Compose stack for the
voicemail app and Jaeger. The app and Jaeger UI bind only to the VPS loopback
interface. Put a TLS reverse proxy in front of the app; Twilio requires public
HTTPS and a long-lived secure WebSocket connection.

1. Point a DNS hostname at the VPS and open inbound ports 80 and 443.
2. Copy the environment template, fill in all credentials, and protect it:

   ```bash
   cp .env.example .env
   chmod 600 .env
   ```

   Set `PUBLIC_HOST` to the bare hostname, such as `voicemail.example.com`.
   Set `NUTSHELL_LEAD_SUBMISSION_ENABLED=true` only when production calls
   should create Nutshell leads.

3. Start the app and Jaeger:

   ```bash
   docker compose up -d --build
   docker compose ps
   curl http://127.0.0.1:7860/status
   ```

4. Install Nginx and Certbot on Ubuntu, then enable the included site. Replace
   `voicemail.example.com` in the copied file with the same hostname used for
   `PUBLIC_HOST`:

   ```bash
   sudo apt update
   sudo apt install -y nginx certbot python3-certbot-nginx
   sudo cp deploy/nginx/voicemail-agent.conf \
       /etc/nginx/sites-available/voicemail-agent
   sudo editor /etc/nginx/sites-available/voicemail-agent
   sudo ln -s /etc/nginx/sites-available/voicemail-agent \
       /etc/nginx/sites-enabled/voicemail-agent
   sudo ufw allow 'Nginx Full'
   sudo nginx -t
   sudo systemctl reload nginx
   sudo certbot --nginx --redirect -d voicemail.example.com
   ```

   The Nginx configuration forwards WebSocket upgrades, gives calls a one-hour
   idle timeout, and exposes only Twilio's `POST /` webhook and `/ws` media
   stream. Port 7860 remains reachable only from the VPS itself.

5. In the Twilio Console, configure the phone number's **A call comes in**
   voice webhook as `https://voicemail.example.com/` with method `POST`.
   Pipecat responds with TwiML that connects the call to
   `wss://voicemail.example.com/ws`.

Tracing is enabled by `ENABLE_TRACING=true`. The app exports OTLP over gRPC to
the Compose `jaeger` service, and traces appear under the `voicemail-agent`
service. The Jaeger UI is intentionally not public; access it through SSH:

```bash
ssh -L 16686:127.0.0.1:16686 user@your-vps
```

Then open <http://localhost:16686>. This single-container Jaeger setup keeps
traces in memory, so traces are lost when Jaeger restarts. Treat traces as
potentially sensitive call data and do not expose the UI publicly.

The container currently uses Pipecat's development runner because it owns the
Twilio `POST /` and `/ws` dispatch flow. This is appropriate for a low-volume,
single-VPS deployment, but it is not horizontally scalable and does not provide
Twilio signature validation or admission control. Keep the reverse proxy in
front of it and move to a production dispatcher before increasing traffic.

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
