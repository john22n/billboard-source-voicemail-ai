from typing import Any, TypedDict

from loguru import logger
from pipecat.flows import FlowManager, NodeConfig

from src.data_source.neon import get_location_pricing
from src.flows.tools import write_audit_event
from src.system_prompts.voicemail import initial_message


class NameCollectionResult(TypedDict):
    name: str


class BusinessCollectionResult(TypedDict):
    business_name: str


class BillboardLocationCollectionResult(TypedDict):
    billboard_location: str
    pricing_found: bool


class EmailCollectionResult(TypedDict):
    email: str


class CallSummaryResult(TypedDict):
    summary: str


async def collect_name(
    flow_manager: FlowManager,
    name: str,
) -> tuple[NameCollectionResult, NodeConfig]:
    """Record the caller's full name.

    Args:
        name: The caller's full name.
    """
    logger.debug("Collected caller name")
    flow_manager.state["name"] = name
    write_audit_event("flow_step_completed", step="name")
    return NameCollectionResult(name=name), create_business_node()


async def collect_business_info(
    flow_manager: FlowManager,
    business_name: str,
) -> tuple[BusinessCollectionResult, NodeConfig]:
    """Record the caller's business name and type.

    Args:
        business_name: The name and type of business being advertised.
    """
    logger.debug("Collected caller business")
    flow_manager.state["business_name"] = business_name
    write_audit_event("flow_step_completed", step="business")
    return (
        BusinessCollectionResult(business_name=business_name),
        create_billboard_location_node(),
    )


async def collect_billboard_location(
    flow_manager: FlowManager,
    billboard_location: str,
) -> tuple[BillboardLocationCollectionResult, NodeConfig]:
    """Record the desired billboard location and look up its pricing.

    Args:
        billboard_location: The city, state, county, or market where the caller
            wants to advertise.
    """
    logger.debug("Collected billboard location")
    flow_manager.state["billboard_location"] = billboard_location
    write_audit_event("flow_step_completed", step="location")
    try:
        pricing = await get_location_pricing(billboard_location)
    except Exception as error:
        write_audit_event(
            "pricing_lookup_failed",
            error_type=type(error).__name__,
            http_status=getattr(error, "status", None),
        )
        logger.exception("Failed to look up billboard pricing")
        pricing = None

    result = BillboardLocationCollectionResult(
        billboard_location=billboard_location,
        pricing_found=pricing is not None,
    )
    if pricing is None:
        write_audit_event("pricing_lookup_not_found")
        return result, create_location_not_found_node(billboard_location)

    flow_manager.state["location_pricing"] = pricing
    write_audit_event("pricing_lookup_succeeded")
    return result, create_pricing_summary_node(pricing)


async def collect_email(
    flow_manager: FlowManager,
    email: str,
) -> tuple[EmailCollectionResult, NodeConfig]:
    """Record the email address after the caller confirms its spelling.

    Args:
        email: The caller's confirmed email address.
    """
    logger.debug("Collected caller email")
    flow_manager.state["email"] = email
    write_audit_event("flow_step_completed", step="email")
    return EmailCollectionResult(email=email), create_summary_node()


async def save_call_summary(
    flow_manager: FlowManager,
    summary: str,
) -> tuple[CallSummaryResult, NodeConfig]:
    """Save a concise CRM summary generated from the completed call.

    Args:
        summary: A factual summary of the caller's request, collected contact
            details, pricing discussed, and required follow-up.
    """
    flow_manager.state["call_summary"] = summary
    write_audit_event("flow_step_completed", step="summary")
    return CallSummaryResult(summary=summary), create_end_node()


def create_initial_node() -> NodeConfig:
    return NodeConfig(
        name="initial",
        role_message=initial_message,
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "Say: Hello, this is Billboard Source. Our team is away, "
                    "but I can collect some information and have someone get "
                    "back to you. May I have your full name? After the caller "
                    "answers, you must immediately call collect_name with their "
                    "answer. Do not continue the conversation without calling it."
                ),
            }
        ],
        functions=[collect_name],
    )


def create_business_node() -> NodeConfig:
    return NodeConfig(
        name="business_name",
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "Ask for the name and type of business the caller wants "
                    "to advertise. After the caller answers, you must immediately "
                    "call collect_business_info with their answer. Do not ask the "
                    "next question yourself."
                ),
            }
        ],
        functions=[collect_business_info],
    )


def create_billboard_location_node() -> NodeConfig:
    return NodeConfig(
        name="billboard_location",
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "Ask where the caller wants to advertise. Request a city, "
                    "state, county, or market such as Denver, Colorado or DFW. "
                    "After the caller answers, you must immediately call "
                    "collect_billboard_location with their answer."
                ),
            }
        ],
        functions=[collect_billboard_location],
    )


def create_location_not_found_node(requested_location: str) -> NodeConfig:
    return NodeConfig(
        name="location_not_found",
        task_messages=[
            {
                "role": "developer",
                "content": (
                    f"No pricing was found for {requested_location}. Explain "
                    "that a representative can research it, then ask for the "
                    "caller's email address. Repeat the address character by "
                    "character and get confirmation. Once confirmed, you must "
                    "immediately call collect_email with the confirmed address."
                ),
            }
        ],
        functions=[collect_email],
    )


def create_pricing_summary_node(pricing: dict[str, Any]) -> NodeConfig:
    location = ", ".join(
        value for value in (pricing.get("city"), pricing.get("state")) if value
    )
    return NodeConfig(
        name="pricing_summary",
        task_messages=[
            {
                "role": "developer",
                "content": (
                    f"Give this general estimate for {location}: average daily "
                    f"views are {pricing.get('avg_daily_views', 'unavailable')} "
                    f"and the four-week range is "
                    f"{pricing.get('four_week_range', 'unavailable')}. Explain "
                    "that final pricing depends on availability. Then ask for "
                    "the caller's email address. Repeat it character by character "
                    "and get confirmation. Once confirmed, you must immediately "
                    "call collect_email with the confirmed address."
                ),
            }
        ],
        functions=[collect_email],
    )


def create_summary_node() -> NodeConfig:
    return NodeConfig(
        name="summarize_call",
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "Silently review the conversation and generate a concise, "
                    "factual CRM summary. Include the caller's request, business, "
                    "target billboard location, pricing discussed, confirmed "
                    "contact details, and follow-up needed. Do not invent missing "
                    "details and do not read the summary aloud. Immediately call "
                    "save_call_summary with the summary."
                ),
            }
        ],
        functions=[save_call_summary],
    )


def create_end_node() -> NodeConfig:
    return NodeConfig(
        name="end",
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "Thank the caller and say that a Billboard Source "
                    "representative will follow up within one business day."
                ),
            }
        ],
        post_actions=[
            {"type": "end_conversation"},
        ],
    )
