from typing import Literal, TypedDict

from pipecat.flows import FlowManager, NodeConfig

from src.flows.tools import submit_nutshell_lead, write_audit_event
from src.system_prompts.voicemail import initial_message


class CollectionResult(TypedDict):
    value: str


async def collect_inquiry_type(
    flow_manager: FlowManager,
    inquiry_type: Literal["advertising", "property"],
) -> tuple[CollectionResult, NodeConfig]:
    """Route the caller based on the purpose of their call.

    Args:
        inquiry_type: Use property only when the caller affirmatively wants to build
            a billboard on property they own. Use advertising when the caller wants
            to advertise a business, campaign, product, service, or message.
    """
    normalized_type = (
        "property"
        if inquiry_type.strip().casefold() in {"property", "property question"}
        else "advertising"
    )
    flow_manager.state["inquiry_type"] = normalized_type
    write_audit_event(
        "flow_step_completed",
        step="inquiry_type",
        inquiry_type=normalized_type,
    )
    next_node = (
        create_property_end_node()
        if normalized_type == "property"
        else create_business_lead_node()
    )
    return CollectionResult(value=normalized_type), next_node


async def collect_business_lead(
    flow_manager: FlowManager,
    full_name: str,
    business_name: str,
    billboard_location: str,
    email: str,
    phone: str | None = None,
) -> tuple[CollectionResult, NodeConfig]:
    """Collect business information and use a supplied callback number when present.

    Args:
        full_name: The caller's first and last name.
        business_name: The name of the caller's business.
        billboard_location: The city and state where they want to advertise.
        email: The caller's email address.
        phone: The caller's callback phone number, if they supplied one.
    """
    full_name = full_name.strip()
    flow_manager.state["name"] = full_name
    flow_manager.state["first_name"] = full_name.split(maxsplit=1)[0]
    flow_manager.state["advertising_type"] = "business"
    flow_manager.state["business_name"] = business_name.strip()
    flow_manager.state["billboard_location"] = billboard_location
    flow_manager.state["email"] = email
    write_audit_event("flow_step_completed", step="business_information")

    if phone and phone.strip():
        return CollectionResult(value=full_name), await _submit_business_lead(
            flow_manager,
            phone.strip(),
        )

    calling_phone = flow_manager.state.get("calling_phone")
    next_node = (
        create_callback_confirmation_node()
        if calling_phone
        else create_callback_number_node()
    )
    return CollectionResult(value=full_name), next_node


async def confirm_callback_number(
    flow_manager: FlowManager,
    is_good_callback: bool,
) -> tuple[CollectionResult, NodeConfig]:
    """Confirm whether the incoming phone number is suitable for callbacks.

    Args:
        is_good_callback: True if the number the caller is using is a good callback.
    """
    if not is_good_callback:
        return CollectionResult(value="no"), create_callback_number_node()

    calling_phone = str(flow_manager.state.get("calling_phone", "")).strip()
    return CollectionResult(value="yes"), await _submit_business_lead(
        flow_manager,
        calling_phone,
    )


async def collect_callback_number(
    flow_manager: FlowManager,
    phone: str,
) -> tuple[CollectionResult, NodeConfig]:
    """Collect a callback number when the incoming number is not suitable.

    Args:
        phone: The caller's preferred callback phone number.
    """
    phone = phone.strip()
    return CollectionResult(value=phone), await _submit_business_lead(
        flow_manager,
        phone,
    )


async def _submit_business_lead(
    flow_manager: FlowManager,
    phone: str,
) -> NodeConfig:
    flow_manager.state["phone"] = phone
    flow_manager.state["call_summary"] = (
        f"{flow_manager.state['name']} from {flow_manager.state['business_name']} "
        f"wants billboard advertising in {flow_manager.state['billboard_location']}. "
        f"Contact: {flow_manager.state['email']}, {phone}."
    )
    write_audit_event("flow_step_completed", step="callback_phone")

    created_lead = await submit_nutshell_lead({}, flow_manager)
    assignee = created_lead.get("assignee", {}) if created_lead else {}
    selected_name = assignee.get("name") if isinstance(assignee, dict) else None
    selected_email = assignee.get("email") if isinstance(assignee, dict) else None
    associate_name = selected_name if isinstance(selected_name, str) else None
    associate_email = selected_email if isinstance(selected_email, str) else None
    flow_manager.state["associate_name"] = associate_name
    flow_manager.state["associate_email"] = associate_email
    return create_associate_followup_node(
        associate_name,
        associate_email,
    )


def create_initial_node() -> NodeConfig:
    return NodeConfig(
        name="initial",
        role_message=initial_message,
        pre_actions=[
            {
                "type": "tts_say",
                "text": (
                    "Thanks for calling Billboard Source, how can I help you? Are you looking to build "
                    "a billboard on your property or advertise your business?"
                ),
            }
        ],
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "You must call collect_inquiry_type before saying anything or "
                    "asking another question. Use property only if the caller "
                    "affirmatively says they want to build a billboard on property "
                    "they own. If they want to advertise their business, campaign, "
                    "product, service, or message, use advertising. If they negate "
                    "the property option, use advertising."
                ),
            }
        ],
        functions=[collect_inquiry_type],
    )


def create_property_end_node() -> NodeConfig:
    return NodeConfig(
        name="property_end",
        task_messages=[],
        pre_actions=[
            {
                "type": "tts_say",
                "text": (
                    "Please Google a local sign company. Thanks for calling "
                    "Billboard Source, goodbye."
                ),
            },
            {"type": "end_conversation"},
        ],
    )


def create_business_lead_node() -> NodeConfig:
    return NodeConfig(
        name="business_lead",
        pre_actions=[
            {
                "type": "tts_say",
                "text": (
                    "Billboard Source can help. All our leasing managers are busy, "
                    "so please leave your full name, business information and contact info."
                ),
            }
        ],
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "Wait for the caller to provide their full name, business name, "
                    "and email address. If they also provide a "
                    "callback phone number, include it; otherwise omit phone. "
                    "Accept the information as provided without repeating or "
                    "confirming it. If anything is missing, ask only for all missing "
                    "required information in one request. Once the four required "
                    "details are available, "
                    "immediately call collect_business_lead."
                ),
            }
        ],
        functions=[collect_business_lead],
        respond_immediately=False,
    )


def create_callback_confirmation_node() -> NodeConfig:
    return NodeConfig(
        name="confirm_callback_number",
        pre_actions=[
            {
                "type": "tts_say",
                "text": (
                    "Is the number you are calling from a good callback number?"
                ),
            }
        ],
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "Immediately call confirm_callback_number with the caller's "
                    "yes or no answer."
                ),
            }
        ],
        functions=[confirm_callback_number],
    )


def create_callback_number_node() -> NodeConfig:
    return NodeConfig(
        name="callback_number",
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "Ask for the caller's best callback phone number, then "
                    "immediately call collect_callback_number."
                ),
            }
        ],
        functions=[collect_callback_number],
    )


def create_associate_followup_node(
    associate_name: str | None,
    associate_email: str | None,
) -> NodeConfig:
    name = associate_name or "the assigned Billboard Source associate"
    email = (
        f" Their email address is {associate_email}."
        if associate_email
        else ""
    )
    return NodeConfig(
        name="associate_followup",
        task_messages=[],
        pre_actions=[
            {
                "type": "tts_say",
                "text": (
                    f"{name} is assigned to your request and will contact you "
                    f"soon.{email} Thanks for calling Billboard Source, goodbye."
                ),
            },
            {"type": "end_conversation"},
        ],
    )
