import json
import os

import aiohttp
from loguru import logger

from src.models.twilio import CallInfo


DEFAULT_WORKSPACE_SID = "WSe6865474d0ee85f098cccf40ade989cb"


async def get_call_info(call_sid: str | None) -> CallInfo | None:
    """Fetch phone numbers from the TaskRouter task for a Twilio call."""
    if not call_sid:
        return None

    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not account_sid or not auth_token:
        logger.warning("Missing Twilio credentials, cannot fetch call info")
        return None

    workspace_sid = os.getenv(
        "TWILIO_TASKROUTER_WORKSPACE_SID",
        DEFAULT_WORKSPACE_SID,
    )
    url = (
        "https://taskrouter.twilio.com/v1/Workspaces/"
        f"{workspace_sid}/Tasks"
    )

    try:
        authorization = aiohttp.encode_basic_auth(account_sid, auth_token)
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                url,
                headers={"Authorization": authorization},
                params={
                    "EvaluateTaskAttributes": (
                        f'(call_sid == "{call_sid}" OR '
                        f'worker_call_sid == "{call_sid}")'
                    ),
                    "PageSize": "1",
                },
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(
                        "Twilio API error ({}): {}",
                        response.status,
                        error_text,
                    )
                    return None

                data = await response.json()
                tasks = data.get("tasks", [])
                if not tasks:
                    logger.warning("No TaskRouter task found for call {}", call_sid)
                    return None

                try:
                    attributes = json.loads(tasks[0].get("attributes") or "{}")
                except json.JSONDecodeError as error:
                    logger.error("Invalid TaskRouter task attributes: {}", error)
                    return None

                return CallInfo(
                    from_number=attributes.get("from") or attributes.get("caller"),
                    to_number=attributes.get("to") or attributes.get("called"),
                )
    except Exception as error:
        logger.error("Error fetching call info from Twilio: {}", error)
        return None
