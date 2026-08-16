import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger
from pipecat.flows import FlowManager
from pipecat.processors.aggregators.llm_context import LLMContext

from src.data_source.nutshell import create_nutshell_lead
from src.models.sales_call_lead import LeadInformation


def write_audit_event(event: str, **details: Any) -> None:
    """Persist a PII-free lifecycle event when local audit logging is enabled."""
    configured_path = os.getenv("VOICEMAIL_AUDIT_LOG")
    if not configured_path:
        return

    path = Path(configured_path)
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **details,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as log_file:
            json.dump(payload, log_file, separators=(",", ":"))
            log_file.write("\n")
        path.chmod(0o600)
    except OSError:
        logger.exception("Failed to write voicemail audit log")


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
            elif isinstance(text, dict) and isinstance(text.get("value"), str):
                parts.append(text["value"])
    return " ".join(parts).strip()


def format_call_transcript(context: LLMContext | None) -> str | None:
    """Format caller and assistant utterances without prompts or tool payloads."""
    if context is None:
        return None

    lines: list[str] = []
    labels = {"user": "Caller", "assistant": "Assistant"}
    for message in context.get_messages():
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in labels:
            continue
        text = _message_text(message.get("content"))
        if text:
            lines.append(f"{labels[role]}: {text}")
    return "\n".join(lines) or None


async def submit_nutshell_lead(
    action: dict,
    flow_manager: FlowManager,
) -> dict[str, Any] | None:
    """Create a Nutshell lead from information collected during the call."""
    if flow_manager.state.get("inquiry_type") == "property" or (
        flow_manager.state.get("lead_collection_agreed") is False
    ):
        write_audit_event("nutshell_submission_skipped", reason="not_advertising_lead")
        logger.info("Skipped Nutshell lead for a non-advertising call")
        return None

    existing_submission = flow_manager.state.get("nutshell_submission_task")
    if isinstance(existing_submission, asyncio.Task):
        try:
            return await asyncio.shield(existing_submission)
        except Exception as error:
            if (
                flow_manager.state.get("nutshell_submission_task")
                is existing_submission
            ):
                flow_manager.state.pop("nutshell_submission_task")
            write_audit_event(
                "nutshell_submission_failed",
                error_type=type(error).__name__,
                http_status=getattr(error, "status", None),
            )
            logger.error(
                "Failed to create Nutshell lead after voicemail call: "
                "error_type={}, http_status={}",
                type(error).__name__,
                getattr(error, "status", None),
            )
        return None

    pricing = flow_manager.state.get("location_pricing") or {}
    notes = flow_manager.state.get("call_summary")
    if not notes and pricing:
        notes = (
            f"Pricing discussed: {pricing.get('four_week_range', 'unavailable')}; "
            f"average daily views: {pricing.get('avg_daily_views', 'unavailable')}."
        )

    context = flow_manager.state.get("llm_context")
    transcript = format_call_transcript(
        context if isinstance(context, LLMContext) else None
    )

    lead = LeadInformation(
        name=flow_manager.state.get("name"),
        business=flow_manager.state.get("business_name"),
        billboard_location=flow_manager.state.get("billboard_location"),
        email=flow_manager.state.get("email"),
        phone=flow_manager.state.get("phone"),
        notes=notes,
        transcript=transcript,
    )

    caller_details = (
        lead.name,
        lead.business,
        lead.billboard_location,
        lead.email,
        lead.phone,
    )
    if not any(value and value.strip() for value in caller_details):
        write_audit_event("nutshell_submission_skipped", reason="no_caller_details")
        logger.info("Skipped Nutshell lead without caller details")
        return None

    submission = asyncio.create_task(create_nutshell_lead(lead))
    flow_manager.state["nutshell_submission_task"] = submission
    write_audit_event(
        "nutshell_submission_started",
        has_name=bool(lead.name),
        has_business=bool(lead.business),
        has_location=bool(lead.billboard_location),
        has_email=bool(lead.email),
        has_phone=bool(lead.phone),
        has_summary=bool(lead.notes),
        has_transcript=bool(lead.transcript),
    )
    try:
        created_lead = await asyncio.shield(submission)
        write_audit_event(
            "nutshell_submission_succeeded",
            lead_id=created_lead.get("id"),
        )
        logger.info("Created Nutshell lead {}", created_lead.get("id"))
        return created_lead
    except Exception as error:
        if flow_manager.state.get("nutshell_submission_task") is submission:
            flow_manager.state.pop("nutshell_submission_task")
        write_audit_event(
            "nutshell_submission_failed",
            error_type=type(error).__name__,
            http_status=getattr(error, "status", None),
        )
        logger.error(
            "Failed to create Nutshell lead after voicemail call: "
            "error_type={}, http_status={}",
            type(error).__name__,
            getattr(error, "status", None),
        )
        return None
