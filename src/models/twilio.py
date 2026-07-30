from pydantic import BaseModel

class CallInfo(BaseModel):
    """Phone details for a Twilio call."""

    from_number: str | None = None
    to_number: str | None = None
