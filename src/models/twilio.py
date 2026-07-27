from pydantic import BaseModel

class CallInfo(BaseModel):
    """Phone details stored on a Twilio TaskRouter task."""

    from_number: str | None = None
    to_number: str | None = None
