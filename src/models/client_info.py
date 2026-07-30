# model the client information to ingest
from pydantic import BaseModel

class ClientInfo(BaseModel):
    """Caller details to send to nutshell after call completed"""

    name: str | None = None
    business: str | None = None
    location: str | list[str] | None = None
    email: str | None = None
    phone: str | None = None
    transcript: str | None = None
