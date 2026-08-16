from typing import TypedDict

from pipecat.flows import FlowManager, NodeConfig

from src.flows.tools import submit_nutshell_lead, write_audit_event
from src.system_prompts.voicemail import initial_message


class CollectionResult(TypedDict):
    value: str


class ConsentResult(TypedDict):
    agreed: bool


class PhoneCollectionResult(TypedDict):
    phone: str


class CallSummaryResult(TypedDict):
    summary: str


async def collect_inquiry_type(
    flow_manager: FlowManager,
    inquiry_type: str,
) -> tuple[CollectionResult, NodeConfig]:
    """Route the caller to the advertising or property inquiry flow.

    Args:
        inquiry_type: Either advertising or property.
    """
    normalized_type = (
        "property" if "propert" in inquiry_type.casefold() else "advertising"
    )
    flow_manager.state["inquiry_type"] = normalized_type
    write_audit_event("flow_step_completed", step="inquiry_type")
    next_node = (
        create_property_company_node()
        if normalized_type == "property"
        else create_advertising_intro_node()
    )
    return CollectionResult(value=normalized_type), next_node


async def collect_property_company(
    flow_manager: FlowManager,
    company_name: str,
) -> tuple[CollectionResult, NodeConfig]:
    """Record the billboard company named by a property caller.

    Args:
        company_name: The billboard company the caller is trying to reach.
    """
    flow_manager.state["property_company"] = company_name
    write_audit_event("flow_step_completed", step="property_company")
    return CollectionResult(value=company_name), create_property_referral_node(
        company_name
    )


async def confirm_lead_collection(
    flow_manager: FlowManager,
    agreed: bool,
) -> tuple[ConsentResult, NodeConfig]:
    """Record whether an advertising caller agrees to leave their information.

    Args:
        agreed: True when the caller agrees to have their information collected.
    """
    flow_manager.state["lead_collection_agreed"] = agreed
    write_audit_event("flow_step_completed", step="lead_collection_consent")
    return ConsentResult(agreed=agreed), (
        create_first_name_node() if agreed else create_declined_node()
    )


async def collect_first_name(
    flow_manager: FlowManager,
    first_name: str,
    last_name: str | None = None,
) -> tuple[CollectionResult, NodeConfig]:
    """Record the advertising caller's first name and any volunteered last name.

    Args:
        first_name: The caller's first name.
        last_name: The caller's last name, if they provided it with their first name.
    """
    flow_manager.state["first_name"] = first_name
    if last_name:
        flow_manager.state["last_name"] = last_name
        flow_manager.state["name"] = f"{first_name} {last_name}".strip()
    write_audit_event("flow_step_completed", step="first_name")
    return CollectionResult(value=first_name), create_advertising_type_node()


async def collect_advertising_type(
    flow_manager: FlowManager,
    advertising_type: str,
) -> tuple[CollectionResult, NodeConfig]:
    """Record what the caller wants to advertise.

    Args:
        advertising_type: Business, political campaign, or personal message.
    """
    flow_manager.state["advertising_type"] = advertising_type
    write_audit_event("flow_step_completed", step="advertising_type")
    return CollectionResult(value=advertising_type), create_advertiser_name_node(
        advertising_type
    )


async def collect_advertiser_name(
    flow_manager: FlowManager,
    advertiser_name: str,
) -> tuple[CollectionResult, NodeConfig]:
    """Record the name of the business, campaign, or personal message.

    Args:
        advertiser_name: The business, campaign, candidate, or message name.
    """
    flow_manager.state["business_name"] = advertiser_name
    write_audit_event("flow_step_completed", step="advertiser_name")
    return CollectionResult(value=advertiser_name), create_billboard_location_node()


async def collect_billboard_location(
    flow_manager: FlowManager,
    billboard_location: str,
) -> tuple[CollectionResult, NodeConfig]:
    """Record the city and state where the caller wants to advertise.

    Args:
        billboard_location: The requested city and state.
    """
    flow_manager.state["billboard_location"] = billboard_location
    write_audit_event("flow_step_completed", step="location")
    return CollectionResult(value=billboard_location), create_email_node()


async def collect_email(
    flow_manager: FlowManager,
    email: str,
) -> tuple[CollectionResult, NodeConfig]:
    """Record the email address after the caller confirms its spelling.

    Args:
        email: The caller's confirmed email address.
    """
    flow_manager.state["email"] = email
    write_audit_event("flow_step_completed", step="email")
    return CollectionResult(value=email), create_phone_confirmation_node(
        flow_manager.state.get("phone")
    )


async def collect_phone(
    flow_manager: FlowManager,
    phone: str,
) -> tuple[PhoneCollectionResult, NodeConfig]:
    """Record the confirmed phone number that is best for reaching the caller.

    Args:
        phone: The caller's confirmed callback phone number.
    """
    flow_manager.state["phone"] = phone
    write_audit_event("flow_step_completed", step="phone")
    next_node = (
        create_summary_node()
        if flow_manager.state.get("last_name")
        else create_last_name_node()
    )
    return PhoneCollectionResult(phone=phone), next_node


async def collect_last_name(
    flow_manager: FlowManager,
    last_name: str,
) -> tuple[CollectionResult, NodeConfig]:
    """Record the caller's last name and complete their full name.

    Args:
        last_name: The caller's last name.
    """
    first_name = str(flow_manager.state.get("first_name", "")).strip()
    flow_manager.state["last_name"] = last_name
    flow_manager.state["name"] = f"{first_name} {last_name}".strip()
    write_audit_event("flow_step_completed", step="last_name")
    return CollectionResult(value=last_name), create_summary_node()


async def save_call_summary(
    flow_manager: FlowManager,
    summary: str,
) -> tuple[CallSummaryResult, NodeConfig]:
    """Save a concise CRM summary generated from the completed call.

    Args:
        summary: A factual summary of the advertising request, collected contact
            details, and required follow-up.
    """
    flow_manager.state["call_summary"] = summary
    created_lead = await submit_nutshell_lead({}, flow_manager)
    assignee = created_lead.get("assignee", {}) if created_lead else {}
    selected_name = assignee.get("name") if isinstance(assignee, dict) else None
    selected_email = assignee.get("email") if isinstance(assignee, dict) else None
    associate_name = selected_name if isinstance(selected_name, str) else None
    associate_email = selected_email if isinstance(selected_email, str) else None
    flow_manager.state["associate_name"] = associate_name
    flow_manager.state["associate_email"] = associate_email
    write_audit_event("flow_step_completed", step="summary")
    return CallSummaryResult(summary=summary), create_associate_followup_node(
        associate_name,
        associate_email,
        str(flow_manager.state.get("first_name", "")) or None,
    )


def create_initial_node() -> NodeConfig:
    return NodeConfig(
        name="initial",
        role_message=initial_message,
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "Say exactly: Billboard Sales, how can I help you? Listen to "
                    "the caller's initial request. Then clarify whether they want "
                    "to advertise with Billboard Source or have a question about "
                    "a billboard property. Once clear, immediately call "
                    "collect_inquiry_type with advertising or property."
                ),
            }
        ],
        functions=[collect_inquiry_type],
    )


def create_property_company_node() -> NodeConfig:
    return NodeConfig(
        name="property_company",
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "Explain warmly that Billboard Source only handles advertising "
                    "requests, then ask for the name of the billboard company the "
                    "caller is trying to reach. After they answer, immediately call "
                    "collect_property_company."
                ),
            }
        ],
        functions=[collect_property_company],
    )


def create_property_referral_node(company_name: str) -> NodeConfig:
    return NodeConfig(
        name="property_referral",
        task_messages=[
            {
                "role": "developer",
                "content": (
                    f"Explain that {company_name} is a different company. Tell the "
                    "caller to search that company name in Google Maps to get the "
                    "most accurate local phone number. Then say exactly: Thanks for "
                    "calling Billboard Source, goodbye. Do not ask whether they have "
                    "other questions or ask any other question."
                ),
            }
        ],
        post_actions=[{"type": "end_conversation"}],
    )


def create_advertising_intro_node() -> NodeConfig:
    return NodeConfig(
        name="advertising_intro",
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "Say that Billboard Source can definitely help. Explain that "
                    "all leasing managers are busy at the moment, but you would be "
                    "happy to collect the caller's information and have one contact "
                    "them shortly. Ask if that would be okay. Immediately call "
                    "confirm_lead_collection with their answer."
                ),
            }
        ],
        functions=[confirm_lead_collection],
    )


def create_declined_node() -> NodeConfig:
    return NodeConfig(
        name="declined",
        task_messages=[
            {
                "role": "developer",
                "content": "Politely thank the caller and end the call.",
            }
        ],
        post_actions=[{"type": "end_conversation"}],
    )


def create_first_name_node() -> NodeConfig:
    return NodeConfig(
        name="first_name",
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "Ask for the caller's first name. After they answer, "
                    "immediately call collect_first_name. If they also volunteer "
                    "their last name, pass it separately as last_name; do not ask "
                    "for their last name here."
                ),
            }
        ],
        functions=[collect_first_name],
    )


def create_advertising_type_node() -> NodeConfig:
    return NodeConfig(
        name="advertising_type",
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "Ask whether the caller wants to advertise a business, a "
                    "political campaign, or a personal message. After they answer, "
                    "immediately call collect_advertising_type."
                ),
            }
        ],
        functions=[collect_advertising_type],
    )


def create_advertiser_name_node(advertising_type: str) -> NodeConfig:
    return NodeConfig(
        name="advertiser_name",
        task_messages=[
            {
                "role": "developer",
                "content": (
                    f"Ask for the name of the {advertising_type}. Adapt the wording "
                    "naturally for a business, campaign, candidate, or personal "
                    "message. After they answer, immediately call "
                    "collect_advertiser_name."
                ),
            }
        ],
        functions=[collect_advertiser_name],
    )


def create_billboard_location_node() -> NodeConfig:
    return NodeConfig(
        name="billboard_location",
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "If the caller already stated the city and state where they "
                    "want to advertise, do not ask for it again; immediately call "
                    "collect_billboard_location with that location. Otherwise, ask "
                    "for the city and state once. Accept their answer as given and "
                    "immediately call collect_billboard_location. Do not repeat, "
                    "confirm, or ask for the location a second time."
                ),
            }
        ],
        functions=[collect_billboard_location],
    )


def create_email_node() -> NodeConfig:
    return NodeConfig(
        name="email",
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "Ask for a good email address. Repeat it character by character "
                    "and get confirmation. Once confirmed, immediately call "
                    "collect_email with the confirmed address."
                ),
            }
        ],
        functions=[collect_email],
    )


def create_phone_confirmation_node(phone: str | None) -> NodeConfig:
    if phone:
        instructions = (
            "Ask whether the phone number they are calling from is the best number "
            "to reach them. If they confirm it is, immediately call collect_phone "
            f"with {phone}. If not, collect and confirm the best number digit by "
            "digit, then call collect_phone with it."
        )
    else:
        instructions = (
            "Ask for the best phone number to reach the caller. Repeat it digit by "
            "digit and get confirmation, then immediately call collect_phone."
        )
    return NodeConfig(
        name="confirm_phone",
        task_messages=[{"role": "developer", "content": instructions}],
        functions=[collect_phone],
    )


def create_last_name_node() -> NodeConfig:
    return NodeConfig(
        name="last_name",
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "Naturally acknowledge the answer, then say you almost forgot "
                    "to ask for the caller's last name. After they answer, "
                    "immediately call collect_last_name."
                ),
            }
        ],
        functions=[collect_last_name],
    )


def create_summary_node() -> NodeConfig:
    return NodeConfig(
        name="summarize_call",
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "Silently generate a concise, factual CRM summary. Include the "
                    "advertising type, advertiser name, target city and state, "
                    "confirmed contact details, and follow-up needed. Do not invent "
                    "missing details or read the summary aloud. Immediately call "
                    "save_call_summary."
                ),
            }
        ],
        functions=[save_call_summary],
    )


def create_associate_followup_node(
    associate_name: str | None,
    associate_email: str | None,
    first_name: str | None,
) -> NodeConfig:
    name = associate_name or "the assigned Billboard Source associate"
    email = (
        f" Give the caller the associate's email address: {associate_email}."
        if associate_email
        else ""
    )
    caller_name = f" {first_name.strip()}" if first_name and first_name.strip() else ""
    return NodeConfig(
        name="associate_followup",
        task_messages=[
            {
                "role": "developer",
                "content": (
                    f"Tell the caller that {name} is the associate assigned to "
                    f"their request and will reach out soon.{email} Thank{caller_name} "
                    "for calling. Do not describe this as a transfer or handoff, and "
                    "do not ask another question."
                ),
            }
        ],
        post_actions=[{"type": "end_conversation"}],
    )


def create_end_node() -> NodeConfig:
    return NodeConfig(
        name="end",
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "Thank the caller and say they should hear from a Billboard "
                    "Source associate shortly."
                ),
            }
        ],
        post_actions=[{"type": "end_conversation"}],
    )
