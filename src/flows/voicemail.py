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
    email: str,
    business_name: str | None = None,
    phone: str | None = None,
) -> tuple[CollectionResult, NodeConfig]:
    """Collect business information and use a supplied callback number when present.

    Args:
        full_name: The caller's first and last name.
        email: The caller's email address.
        business_name: The caller's business name. Omit it when the caller has not
            provided one; never use a placeholder such as "not provided".
        phone: The caller's callback phone number, if supplied. Use "this number"
            when the caller wants a callback at the number they are calling from.
    """
    full_name = full_name.strip()
    business_name = business_name.strip() if business_name else ""
    if business_name.casefold() in {
        "n/a",
        "none",
        "not given",
        "not provided",
        "unknown",
    }:
        business_name = ""
    email = email.strip()
    flow_manager.state["name"] = full_name
    flow_manager.state["first_name"] = full_name.split(maxsplit=1)[0]
    flow_manager.state["advertising_type"] = "business"
    flow_manager.state["business_name"] = business_name
    flow_manager.state["email"] = email

    supplied_phone = phone.strip() if phone else ""
    if supplied_phone:
        uses_calling_phone = _uses_calling_phone(supplied_phone)
        callback_phone = (
            str(flow_manager.state.get("calling_phone", "")).strip()
            if uses_calling_phone
            else supplied_phone
        )
        if callback_phone:
            _stage_callback_phone(
                flow_manager,
                callback_phone,
                uses_calling_phone=uses_calling_phone,
            )

    if not business_name:
        return CollectionResult(value=full_name), create_business_lead_node(
            request_business_name_only=True
        )

    write_audit_event("flow_step_completed", step="business_information")

    pending_phone = str(
        flow_manager.state.get("pending_callback_phone", "")
    ).strip()
    if not pending_phone:
        calling_phone = str(flow_manager.state.get("calling_phone", "")).strip()
        if calling_phone:
            _stage_callback_phone(
                flow_manager,
                calling_phone,
                uses_calling_phone=True,
            )
            pending_phone = calling_phone

    if pending_phone:
        return CollectionResult(value=full_name), create_callback_confirmation_node(
            pending_phone,
            uses_calling_phone=bool(
                flow_manager.state.get("pending_callback_uses_calling_phone")
            ),
        )

    return CollectionResult(value=full_name), create_callback_number_node()


async def confirm_callback_number(
    flow_manager: FlowManager,
    is_good_callback: bool,
) -> tuple[CollectionResult, NodeConfig]:
    """Confirm whether the staged phone number is suitable for callbacks.

    Args:
        is_good_callback: True if the number just presented is a good callback.
    """
    if not is_good_callback:
        flow_manager.state.pop("pending_callback_phone", None)
        flow_manager.state.pop("pending_callback_uses_calling_phone", None)
        return CollectionResult(value="no"), create_callback_number_node()

    pending_phone = str(
        flow_manager.state.get("pending_callback_phone", "")
    ).strip()
    if not pending_phone:
        return CollectionResult(value="yes"), create_callback_number_node()

    next_node = await _submit_business_lead(
        flow_manager,
        pending_phone,
    )
    flow_manager.state.pop("pending_callback_phone", None)
    flow_manager.state.pop("pending_callback_uses_calling_phone", None)
    return CollectionResult(value="yes"), next_node


async def collect_callback_number(
    flow_manager: FlowManager,
    phone: str,
) -> tuple[CollectionResult, NodeConfig]:
    """Collect a callback number and ask the caller to confirm it.

    Args:
        phone: The caller's preferred callback phone number.
    """
    phone = phone.strip()
    if not phone:
        return CollectionResult(value=phone), create_callback_number_node()

    _stage_callback_phone(
        flow_manager,
        phone,
        uses_calling_phone=False,
    )
    return CollectionResult(value=phone), create_callback_confirmation_node(
        phone,
        uses_calling_phone=False,
    )


def _uses_calling_phone(phone: str) -> bool:
    normalized = phone.casefold().replace("’", "'").rstrip(" .!?")
    return normalized in {
        "this number",
        "this phone number",
        "the number i'm calling from",
        "the number i am calling from",
    }


def _stage_callback_phone(
    flow_manager: FlowManager,
    phone: str,
    *,
    uses_calling_phone: bool,
) -> None:
    flow_manager.state["pending_callback_phone"] = phone
    flow_manager.state["pending_callback_uses_calling_phone"] = uses_calling_phone


async def _submit_business_lead(
    flow_manager: FlowManager,
    phone: str,
) -> NodeConfig:
    calling_phone = str(flow_manager.state.get("calling_phone", "")).strip()
    flow_manager.state["phone"] = phone
    flow_manager.state["call_summary"] = (
        f"{flow_manager.state['name']} from {flow_manager.state['business_name']} "
        "wants billboard advertising. "
        f"Contact: {flow_manager.state['email']}, {phone}."
        + (f" Twilio caller number: {calling_phone}." if calling_phone else "")
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
        name="start",
        role_message=initial_message,
        pre_actions=[
            {
                "type": "tts_say",
                "text": (
                    "Thanks for calling Billboard Source, how can I help you? "
                    "Are you looking to build a billboard on your property or "
                    "advertise your business?"
                ),
            }
        ],
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "ask if they are calling about a billboard on thier property or if they want to advertise their business, route them correcly to the next step based their response"
                ),
            }
        ],
        functions=[collect_inquiry_type],
        respond_immediately=False,
    )


def create_property_end_node() -> NodeConfig:
    return NodeConfig(
        name="property_end",
        task_messages=[],
        pre_actions=[
            {
                "type": "tts_say",
                "text": (
                    "Please Google a local sign company. Billboard Source helps "
                    "clients find billboards to advertise on. Thanks for calling, "
                    "goodbye."
                ),
            },
            {"type": "end_conversation"},
        ],
        respond_immediately=False,
    )


def create_business_lead_node(
    *,
    request_business_name_only: bool = False,
) -> NodeConfig:
    if request_business_name_only:
        request_text = "What is the name of your business?"
        task_content = (
            "Wait for the caller to provide their business name, then immediately "
            "call collect_business_lead using it with the previously provided full "
            "name, email address, and optional callback preference. The business "
            "name must not be blank."
        )
    else:
        request_text = (
            "Billboard Source can help. All our leasing managers are busy, "
            "so please leave your full name, business information, and "
            "contact info."
        )
        task_content = (
            "Wait for the caller to provide their full name, business name, "
            "and email address. If they also provide a callback phone "
            "number, include it; otherwise omit phone. If they ask to be called "
            'back at this number, pass phone as "this number". '
            "Accept the information as provided without repeating or "
            "confirming it. If anything is missing, ask only for all missing "
            "required information in one request. Once those three required "
            "details are available, immediately call collect_business_lead."
        )

    return NodeConfig(
        name="business_lead",
        pre_actions=[
            {
                "type": "tts_say",
                "text": request_text,
            }
        ],
        task_messages=[
            {
                "role": "developer",
                "content": task_content,
            }
        ],
        functions=[collect_business_lead],
        respond_immediately=False,
    )


def create_callback_confirmation_node(
    phone: str,
    *,
    uses_calling_phone: bool,
) -> NodeConfig:
    question = (
        "Is the number you are calling from a good callback number?"
        if uses_calling_phone
        else f"Is {phone} the correct callback number?"
    )
    return NodeConfig(
        name="confirm_callback_number",
        pre_actions=[
            {
                "type": "tts_say",
                "text": question,
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
        respond_immediately=False,
    )


def create_callback_number_node() -> NodeConfig:
    return NodeConfig(
        name="callback_number",
        pre_actions=[
            {
                "type": "tts_say",
                "text": "What is your best callback phone number?",
            }
        ],
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "Immediately call collect_callback_number after the caller "
                    "provides their callback phone number."
                ),
            }
        ],
        functions=[collect_callback_number],
        respond_immediately=False,
    )


def create_associate_followup_node(
    associate_name: str | None,
    associate_email: str | None,
) -> NodeConfig:
    name = associate_name or "A Billboard Source associate"
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
        respond_immediately=False,
    )
