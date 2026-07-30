import os

import aiohttp
from loguru import logger

from src.models.twilio import CallInfo


async def get_call_info(call_sid: str | None) -> CallInfo | None:
    """Fetch phone numbers directly from Twilio's Calls API."""
    if not call_sid:
        return None

    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not account_sid or not auth_token:
        logger.warning("Missing Twilio credentials, cannot fetch call info")
        return None

    url = (
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/"
        f"Calls/{call_sid}.json"
    )

    try:
        authorization = aiohttp.encode_basic_auth(account_sid, auth_token)
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                url,
                headers={"Authorization": authorization},
            ) as response:
                if response.status != 200:
                    logger.error("Twilio Calls API error ({})", response.status)
                    return None

                data = await response.json()
                return CallInfo(
                    from_number=data.get("from"),
                    to_number=data.get("to"),
                )
    except Exception as error:
        logger.error("Error fetching call info from Twilio: {}", error)
        return None
