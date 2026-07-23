from pydantic import BaseModel

class LeadInformation(BaseModel):
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    business: str | None = None
    billboard_location: str | None = None
    notes: str | None = None
    transcript: str | None = None
