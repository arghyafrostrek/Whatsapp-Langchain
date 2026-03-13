import json
import time
from typing import Any, Dict, List, Optional

from langchain_openai import ChatOpenAI

from logger_config import logger
from knowledge_base import get_company_knowledge
from config import settings


def _safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    try:
        loaded = json.loads(text)
        return loaded if isinstance(loaded, dict) else None
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        loaded = json.loads(text[start : end + 1])
        return loaded if isinstance(loaded, dict) else None
    except Exception:
        return None


def extract_intent_with_llm(message: str, history: List[str], summary: str = "", session_id: str = "") -> Dict[str, Any]:
    """
    LLM-based intent + data extraction with sliding window memory
    and structured user profile injection.
    Returns a dict parsed from strict JSON. On failure, returns fallback.
    Does not modify application state.
    """

    fallback: Dict[str, Any] = {
        "intent": "other",
        "reply": "Sorry, I couldn't understand that.",
        "meeting_date": None,
        "meeting_time": None,
        "meeting_title": None,
        "participant_name": None,
        "email_address": None,
    }

    if not message or not message.strip():
        return fallback

    if not settings.OPENAI_API_KEY:
        return fallback

    # ---- Step 1: Load sliding window history (last 10 messages) ----
    from session_store import get_session_history
    from memory.summary import get_conversation_summary as _get_summary
    db_history = get_session_history(session_id) if session_id else []
    recent_history = db_history[-10:] if db_history else []

    # Build context messages with proper role attribution
    context_messages = []
    for msg in recent_history:
        context_messages.append({
            "role": msg["role"],
            "content": msg["content"]
        })

    # ---- Step 2: Load user profile ----
    from memory.user_profile import get_user_profile
    profile = get_user_profile(session_id) if session_id else {}

    # ---- Build summary text ----
    # Fetch from DB if not provided by caller
    if not summary and session_id:
        summary = _get_summary(session_id)
    summary_text = f"Conversation summary (older context):\n{summary}\n\n" if summary else ""

    company_knowledge = get_company_knowledge()

    from core.system_prompt import FROSTY_SYSTEM_PROMPT

    system_prompt = f"""
{FROSTY_SYSTEM_PROMPT}

You are also an intent and data extraction engine.

Use the following company knowledge as the ground truth context for your response:
{company_knowledge if company_knowledge else "(no company knowledge available)"}

{summary_text}Return STRICT JSON ONLY in this exact format, with no extra text:

{{
  "intent": "schedule_meeting | send_email | confirmation_reply | normal_chat | other",
  "reply": "string",
  "meeting_date": "string or null",
  "meeting_time": "string or null",
  "meeting_title": "string or null",
  "participant_name": "string or null",
  "email_address": "string or null"
}}
""".strip()

    # ---- Step 3: Build final messages list ----
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": system_prompt}
    ]

    # Inject profile right after system prompt (Step 3 from spec)
    if profile and any(profile.values()):
        profile_content = "User Profile:\n"
        if profile.get("name"):
            profile_content += f"Name: {profile['name']}\n"
        if profile.get("email"):
            profile_content += f"Email: {profile['email']}\n"
        if profile.get("preferred_language"):
            profile_content += f"Preferred Language: {profile['preferred_language']}\n"
        if profile.get("timezone"):
            profile_content += f"Timezone: {profile['timezone']}\n"

        messages.append({"role": "system", "content": profile_content.strip()})

    # Append sliding window history
    messages.extend(context_messages)

    # Append current user message
    messages.append({"role": "user", "content": message})

    # ---- Step 5: Call LLM with response time logging ----
    try:
        logger.info(
            "llm_intent_extract_start message_len=%s history_len=%s recent_window=%s",
            len(message),
            len(db_history),
            len(recent_history),
        )

        start_time = time.time()

        try:
            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2, timeout=20)
        except TypeError:
            try:
                llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2, request_timeout=20)
            except TypeError:
                llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)

        response = llm.invoke(messages)

        elapsed = time.time() - start_time
        logger.info("LLM response time: %.2f seconds", elapsed)

        content = response.content
        if not isinstance(content, str):
            content = json.dumps(content)

        parsed = _safe_json_loads(content)
        if not parsed:
            logger.info("llm_intent_extract_fallback json_parse_failed=true")
            return fallback

        # Ensure required keys exist even if model omits them
        parsed.setdefault("intent", "other")
        parsed.setdefault("reply", "Sorry, I couldn't understand that.")
        parsed.setdefault("meeting_date", None)
        parsed.setdefault("meeting_time", None)
        parsed.setdefault("meeting_title", None)
        parsed.setdefault("participant_name", None)
        parsed.setdefault("email_address", None)

        return parsed
    except Exception:
        logger.info("llm_intent_extract_fallback exception=true")
        return fallback
