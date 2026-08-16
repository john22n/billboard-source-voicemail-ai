import asyncio
import os
import re
from typing import Any

import aiohttp

from src.models.sales_call_lead import LeadInformation


NUTSHELL_API_URL = "https://app.nutshell.com/rest"


async def _nutshell_request(
    session: aiohttp.ClientSession,
    method: str,
    path: str,
    headers: dict[str, str],
    *,
    params: dict[str, Any] | None = None,
    payload: Any | None = None,
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


async def _find_or_create_account(
    session: aiohttp.ClientSession,
    headers: dict[str, str],
    business: str | None,
) -> str | None:
    if not business or not (name := business.strip()):
        return None

    # Nutshell returns HTTP 500 when a name filter contains commas. Remove them
    # only from the lookup value; preserve the caller's business name on create.
    lookup_name = re.sub(r",\s*", " ", name)
    data = await _nutshell_request(
        session,
        "GET",
        "accounts",
        headers,
        params={"filter[name]": lookup_name, "page[limit]": 100},
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


def _phone_text(phone: dict[str, Any]) -> str:
    value = phone.get("value", "")
    if isinstance(value, dict):
        return str(
            value.get("E164")
            or value.get("countryCodeAndNumber")
            or value.get("number")
            or ""
        )
    return str(value)


async def _add_phone_to_contact(
    session: aiohttp.ClientSession,
    headers: dict[str, str],
    contact: dict[str, Any],
    phone: str | None,
) -> None:
    if not phone:
        return
    phone = phone.strip()
    if not phone:
        return

    phones = contact.get("phones", [])
    normalized_phone = re.sub(r"\D", "", phone)
    if any(
        re.sub(r"\D", "", _phone_text(existing)) == normalized_phone
        for existing in phones
    ):
        return

    updated_phones = [
        *phones,
        {"value": phone, "isPrimary": not phones},
    ]
    await _nutshell_request(
        session,
        "PATCH",
        f"contacts/{contact['id']}",
        {**headers, "Content-Type": "application/json-patch+json"},
        payload=[
            {
                "op": "replace",
                "path": "/contacts/0/phones",
                "value": updated_phones,
            }
        ],
    )


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
                await _add_phone_to_contact(
                    session,
                    headers,
                    existing,
                    lead.phone,
                )
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


def _first_resource(data: Any, resource: str) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    resources = data.get(resource)
    if (
        isinstance(resources, list)
        and resources
        and isinstance(resources[0], dict)
    ):
        return resources[0]
    return data if isinstance(data.get("id"), str) else None


def _linked_id(resource: dict[str, Any], relationship: str) -> str | None:
    links = resource.get("links")
    if not isinstance(links, dict):
        return None
    linked = links.get(relationship)
    if isinstance(linked, str):
        return linked
    if isinstance(linked, dict) and isinstance(linked.get("id"), str):
        return linked["id"]
    if isinstance(linked, list) and linked:
        first = linked[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict) and isinstance(first.get("id"), str):
            return first["id"]
    return None


async def _get_round_robin_assignee(
    session: aiohttp.ClientSession,
    headers: dict[str, str],
    lead_id: str,
) -> dict[str, str] | None:
    lead_data = await _nutshell_request(
        session,
        "GET",
        f"leads/{lead_id}",
        headers,
    )
    lead = _first_resource(lead_data, "leads")
    owner_id = _linked_id(lead, "owner") if lead else None
    if not owner_id:
        return None

    user_data = await _nutshell_request(
        session,
        "GET",
        f"users/{owner_id}",
        headers,
    )
    user = _first_resource(user_data, "users")
    if not user:
        return None

    name = user.get("firstName") or user.get("name")
    emails = user.get("emails")
    email = emails[0] if isinstance(emails, list) and emails else None
    return {
        key: value
        for key, value in (("id", owner_id), ("name", name), ("email", email))
        if isinstance(value, str) and value
    }


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

        assignee = await _get_round_robin_assignee(
            session,
            headers,
            lead_id,
        )
        if assignee:
            created_lead["assignee"] = assignee

        note_sections = []
        if lead.notes and lead.notes.strip():
            note_sections.append(f"--- CALL SUMMARY ---\n\n{lead.notes.strip()}")
        if lead.transcript and lead.transcript.strip():
            note_sections.append(
                f"--- CALL TRANSCRIPT ---\n\n{lead.transcript.strip()}"
            )
        if note_sections:
            await _nutshell_request(
                session,
                "POST",
                "notes",
                headers,
                payload={
                    "data": {
                        "body": "\n\n".join(note_sections),
                        "links": {"parent": lead_id},
                    }
                },
            )

        return created_lead
