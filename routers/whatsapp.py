"""
WhatsApp Cloud API Webhook Router.
"""

import os
import json
import asyncio
from typing import Any

from fastapi import APIRouter, Request, Response, BackgroundTasks
from fastapi.responses import PlainTextResponse

from logger_config import logger
from db_config import get_connection, release_connection
from services.whatsapp import send_text_message, send_audio_message, mark_as_read
from services.input_router import route_input
from services.intent_classifier import classify_intent
from services.slack_notifier import notify_new_lead
from session_store import (
    save_message,
    get_session_state,
    save_session_state,
    get_user_profile,
    update_user_profile,
)
from state import State, SessionInfo, InputMessage, IntentPayload
from main import chat_router

router = APIRouter(prefix="/webhook/whatsapp", tags=["WhatsApp"])


@router.get("")
async def verify_webhook(request: Request):
    """
    Step 1: Meta webhook verification.
    """
    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode and token:
        if mode == "subscribe" and token == verify_token:
            logger.info("whatsapp_webhook: verification successful")
            return PlainTextResponse(challenge, status_code=200)
        else:
            logger.warning("whatsapp_webhook: verification failed (token mismatch)")
            return Response("Forbidden", status_code=403)

    return Response("Bad Request", status_code=400)


@router.post("")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Step 2: Receive messages from WhatsApp.
    """
    try:
        body = await request.json()
    except json.JSONDecodeError:
        logger.error("whatsapp_webhook: invalid json payload")
        return Response("Bad Request", status_code=400)

    # Acknowledge Receipt Immediately
    if not _is_whatsapp_message_event(body):
        return Response("OK", status_code=200)

    # Process messages in background so we don't block the webhook response
    background_tasks.add_task(_process_whatsapp_payload, body)
    
    return Response("OK", status_code=200)


def _is_whatsapp_message_event(payload: dict) -> bool:
    """Check if the payload contains actual user messages."""
    try:
        if payload.get("object") != "whatsapp_business_account":
            return False
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if "messages" in value:
                    return True
        return False
    except Exception:
        return False


async def _process_whatsapp_payload(payload: dict):
    """
    Parse the Meta payload and run the LangGraph agent for each message.
    """
    entries = payload.get("entry", [])
    for entry in entries:
        changes = entry.get("changes", [])
        for change in changes:
            value = change.get("value", {})
            messages = value.get("messages", [])
            contacts = value.get("contacts", [])
            phone_number_id = value.get("metadata", {}).get("phone_number_id")

            contact_dict = {
                c.get("wa_id"): c.get("profile", {}).get("name", "")
                for c in contacts
            }

            for msg in messages:
                try:
                    await _process_single_message(msg, contact_dict, phone_number_id)
                except Exception as exc:
                    logger.error("whatsapp_webhook: error processing message: %s", exc)


async def _process_single_message(msg: dict, contact_dict: dict[str, str], phone_number_id: str):
    """Run the pipeline for a single WhatsApp incoming message."""
    wa_id = msg.get("from")  # user's phone number
    msg_id = msg.get("id")
    
    if not wa_id or not msg_id:
        return

    # Mark as read (async fire-and-forget or sync wrapper)
    # the httpx call inside is synchronous, so we run it in threadpool
    await asyncio.to_thread(mark_as_read, msg_id, phone_number_id)

    # 1. Route Input (download media, transcribe, normalize)
    input_data = await asyncio.to_thread(route_input, msg)
    text = input_data.get("text", "")
    input_type = input_data.get("input_type", "text")
    is_voice = (input_type == "voice")
    
    # 2. Extract Session and Profile
    session_id = f"wa_{wa_id}"
    user_name = contact_dict.get(wa_id, "")

    profile = get_user_profile(session_id)
    if not profile or not profile.get("phone"):
        profile.update({"phone": wa_id, "name": user_name or profile.get("name", "")})
        update_user_profile(session_id, profile)

    user_email = profile.get("email", "")

    # User messages are now saved via LangGraph nodes (chat_router -> nodes.py)
    # to prevent duplicates, we don't save them here.

    # 3. Classify Intent
    intent_label = await asyncio.to_thread(classify_intent, text)

    # 4. Prepare LangGraph State
    saved_state = get_session_state(session_id)
    if not saved_state:
        saved_state = _create_initial_state(session_id, user_email, user_name)

    # Inject new turn data
    saved_state["input"] = {
        "input_type": input_type,
        "raw_message": text,
        "normalized_text": text,
        "is_voice": is_voice,
    }
    saved_state["intent"] = {"intent": intent_label}
    
    # 5. Run LangGraph Agent
    logger.info("whatsapp_webhook: calling chat_router for %s", session_id)
    try:
        # chat_router is completely synchronous in main.py
        result = await asyncio.to_thread(
            chat_router,
            session_id,
            saved_state,
            text,  # user_message
        )
    except Exception as exc:
        logger.error("whatsapp_webhook: agent crash: %s", exc)
        result = "I'm having a technical issue right now, please give me a moment."

    # Parse result into dict if it's a JSON/stringified dict
    parsed_result = {}
    if isinstance(result, dict):
        parsed_result = result
    elif isinstance(result, str):
        try:
            parsed_result = json.loads(result)
        except json.JSONDecodeError:
            try:
                import ast
                parsed_dict = ast.literal_eval(result)
                if isinstance(parsed_dict, dict):
                    parsed_result = parsed_dict
            except Exception:
                pass

    if parsed_result and isinstance(parsed_result, dict):
        reply_text = parsed_result.get("reply") or parsed_result.get("chat_reply") or str(result)
    else:
        reply_text = str(result)

    # 6. Send Reply (Voice or Text)
    if is_voice:
        try:
            from tools.tts_tool import text_to_speech
            import tempfile
            
            # Generate MP3 bytes
            audio_bytes = await text_to_speech(reply_text)
            
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                f.write(audio_bytes)
                audio_path = f.name
                
            send_success = await asyncio.to_thread(send_audio_message, wa_id, audio_path, phone_number_id)
            
            try:
                os.unlink(audio_path)
            except Exception:
                pass
                
            if not send_success:
                # Fallback to text if audio upload/send failed
                send_success = await asyncio.to_thread(send_text_message, wa_id, reply_text, phone_number_id)
                
        except Exception as exc:
            logger.error("whatsapp_webhook: voice reply generation failed: %s", exc)
            send_success = await asyncio.to_thread(send_text_message, wa_id, reply_text, phone_number_id)
    else:
        send_success = await asyncio.to_thread(send_text_message, wa_id, reply_text, phone_number_id)
    
    # Bot messages are also saved by LangGraph nodes, skipping here.

    # 7. Post-processing (Lead Capture Alert)
    _check_and_alert_lead(session_id, parsed_result, wa_id, text)


def _create_initial_state(session_id: str, email: str, name: str) -> dict:
    """Create a fresh state dict matching state.py TypedDicts."""
    return {
        "session": {
            "session_id": session_id,
            "user_email": email,
            "display_name": name,
        },
        "input": {"input_type": "text", "raw_message": "", "is_voice": False},
        "intent": {"intent": "normal_chat"},
        "email_flow": {
            "draft": {"has_draft": False},
            "pending_confirmation": {"awaiting_confirmation": False},
            "sent": {"sent": False},
        },
        "meeting_flow": {"validated": False},
        "voice": {"is_voice_input": False},
        "model_io": {},
        "scratch": {},
    }


def _check_and_alert_lead(session_id: str, result: dict, phone: str, query: str):
    """If the agent extracted an email this turn, fire a Slack alert."""
    try:
        if "lead_data" in result:
            lead = result["lead_data"]
            if not lead.get("follow_up_sent"):
                notify_new_lead(
                    tenant_id="default",
                    name=lead.get("name", ""),
                    email=lead.get("email", ""),
                    phone=lead.get("phone", phone),
                    interest=lead.get("interest", query),
                    query=query,
                )
                # Mark as handled in Postgres (would normally require a lead table update)
                # But here we just fire and forget for the demo scope
    except Exception as exc:
        logger.error("whatsapp_webhook: lead alert failed: %s", exc)
