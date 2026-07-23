import asyncio
import os
import random
import re
from typing import Any

import aiohttp

from src.models.sales_call_lead import LeadInformation


NUTSHELL_API_URL = "https://app.nutshell.com/rest"
ASHTON_EMAIL = "ashton@billboardsource.com"


async def _nutshell_request(
    session: aiohttp.ClientSession,
    method: str,
    path: str,
    headers: dict[str, str],
    *,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    retries: int = 2,
) -> Any:
    request_options: dict[str, Any] = {"headers": headers}
    if params:
        request_options["params"] = params
    if payload is not None:
        request_options["json"] = payload

    for attempt in range(retries + 1):
        try:
            async with session.request(
                method,
                f"{NUTSHELL_API_URL}/{path}",
                **request_options,
            ) as response:
                response.raise_for_status()
                if response.status == 204:
                    return None
                return await response.json()
        except aiohttp.ClientError:
            if method.upper() not in {"GET", "HEAD"} or attempt == retries:
                raise
            await asyncio.sleep(attempt + 1)

    raise RuntimeError(f"Nutshell {method} {path} failed")


def _valid_email(email: str | None) -> str | None:
    if not email:
        return None
    email = email.strip()
    return email if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) else None


async def _resolve_assignee_id(
    session: aiohttp.ClientSession,
    headers: dict[str, str],
    default_email: str,
) -> str:
    assignee_email = ASHTON_EMAIL if random.randrange(3) == 0 else default_email
    data = await _nutshell_request(session, "GET", "users", headers)
    for user in data.get("users", []):
        if any(
            email.lower() == assignee_email.lower()
            for email in user.get("emails", [])
        ):
            return user["id"]
    raise RuntimeError(f"No Nutshell user found for email: {assignee_email}")


async def _find_or_create_account(
    session: aiohttp.ClientSession,
    headers: dict[str, str],
    business: str | None,
) -> str | None:
    if not business or not (name := business.strip()):
        return None

    data = await _nutshell_request(
        session,
        "GET",
        "accounts",
        headers,
        params={"filter[name]": name, "page[limit]": 100},
    )
    for account in data.get("accounts", []):
        if account.get("name", "").lower() == name.lower():
            return account["id"]

    created = await _nutshell_request(
        session,
        "POST",
        "accounts",
        headers,
        payload={"accounts": [{"name": name}]},
    )
    return created["accounts"][0]["id"]


def _contact_payload(lead: LeadInformation, email: str | None) -> dict[str, Any]:
    contact: dict[str, Any] = {}
    if lead.name and lead.name.strip():
        contact["name"] = lead.name.strip()
    if lead.phone and lead.phone.strip():
        contact["phones"] = [{"value": lead.phone.strip(), "isPrimary": True}]
    if email:
        contact["emails"] = [{"value": email, "isPrimary": True}]
    return contact


async def _find_or_create_contact(
    session: aiohttp.ClientSession,
    headers: dict[str, str],
    lead: LeadInformation,
    account_id: str | None,
) -> str | None:
    email = _valid_email(lead.email)
    contact = _contact_payload(lead, email)
    if not contact:
        return None

    if email:
        data = await _nutshell_request(
            session,
            "GET",
            "contacts",
            headers,
            params={"filter[email]": email, "page[limit]": 100},
        )
        for existing in data.get("contacts", []):
            if any(
                item.get("value", "").casefold() == email.casefold()
                for item in existing.get("emails", [])
            ):
                return existing["id"]

    if account_id:
        contact["links"] = {"accounts": [account_id]}
    created = await _nutshell_request(
        session,
        "POST",
        "contacts",
        headers,
        payload={"contacts": [contact]},
    )
    return created["contacts"][0]["id"]


async def _resolve_pipeline_id(
    session: aiohttp.ClientSession,
    headers: dict[str, str],
) -> str | None:
    data = await _nutshell_request(session, "GET", "stagesets", headers)
    pipeline = next(
        (
            item
            for item in data.get("stagesets", [])
            if item.get("name") == "NEW BSI Pipeline"
        ),
        None,
    )
    return pipeline["id"] if pipeline else None


async def _find_or_create_source(
    session: aiohttp.ClientSession,
    headers: dict[str, str],
) -> str:
    name = "AI Voice"
    data = await _nutshell_request(
        session,
        "GET",
        "sources",
        headers,
        params={"q": name},
    )
    for source in data.get("sources", []):
        if source.get("name", "").lower() == name.lower():
            return source["id"]

    created = await _nutshell_request(
        session,
        "POST",
        "sources",
        headers,
        payload={"sources": [{"name": name}]},
    )
    return created["sources"][0]["id"]


async def create_nutshell_lead(lead: LeadInformation) -> dict[str, Any]:
    """Map voicemail lead information into linked Nutshell REST resources."""
    user_email = os.getenv("NUTSHELL_EMAIL")
    api_key = os.getenv("NUTSHELL_API_KEY")
    if not user_email or not api_key:
        raise RuntimeError("NUTSHELL_EMAIL and NUTSHELL_API_KEY are required")

    headers = {
        "Accept": "application/json",
        "Authorization": aiohttp.encode_basic_auth(user_email, api_key),
    }
    timeout = aiohttp.ClientTimeout(total=15)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        assignee_id = await _resolve_assignee_id(session, headers, user_email)
        account_id = await _find_or_create_account(session, headers, lead.business)
        contact_id = await _find_or_create_contact(
            session,
            headers,
            lead,
            account_id,
        )
        pipeline_id = await _resolve_pipeline_id(session, headers)
        source_id = await _find_or_create_source(session, headers)

        description = (
            (lead.business or "").strip()
            or (lead.name or "").strip()
            or "Billboard Lead"
        )
        links: dict[str, Any] = {
            "owner": assignee_id,
            "sources": [source_id],
        }
        if account_id:
            links["accounts"] = [account_id]
        if contact_id:
            links["contacts"] = [contact_id]

        nutshell_lead: dict[str, Any] = {
            "description": description,
            "links": links,
        }
        custom_fields = {}
        if lead.billboard_location and lead.billboard_location.strip():
            custom_fields["Target Market(s) - City/State/Area"] = (
                lead.billboard_location.strip()
            )
        if lead.notes and lead.notes.strip():
            custom_fields["Notes:"] = lead.notes.strip()
        if custom_fields:
            nutshell_lead["customFields"] = custom_fields

        created = await _nutshell_request(
            session,
            "POST",
            "leads",
            headers,
            payload={"leads": [nutshell_lead]},
        )
        created_lead = created["leads"][0]
        lead_id = created_lead["id"]

        if pipeline_id:
            await _nutshell_request(
                session,
                "POST",
                f"leads/{lead_id}/stageset",
                headers,
                payload={"stageset": pipeline_id},
            )

        if lead.transcript and lead.transcript.strip():
            await _nutshell_request(
                session,
                "POST",
                "notes",
                headers,
                payload={
                    "data": {
                        "body": f"--- CALL TRANSCRIPT ---\n\n{lead.transcript.strip()}",
                        "links": {"parent": lead_id},
                    }
                },
            )

        return created_lead
