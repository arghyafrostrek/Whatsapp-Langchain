import os
import re
import time
from typing import Optional, cast

from pydantic import BaseModel, ValidationError
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from config import settings
from services.bot_config import get_llm
from logger_config import logger
from session_store import get_session_history, save_message
from state import State, IntentType
from tools.calendar_tool import (
    check_availability,
    find_free_slots,
    create_meeting
)
from tools.email_tool import send_email
from tools.llm_tool import extract_intent_with_llm

from services.slack_notifier import (
    notify_new_lead,
    notify_meeting_booked,
    notify_email_sent,
)


EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

import json as _json
import re as _re

def safe_json_parse(text: str) -> dict:
    raw = text or ""
    raw = raw.strip()
    
    # Remove markdown fences if present
    raw = raw.replace("```json", "")
    raw = raw.replace("```", "")
    raw = raw.strip()
    
    # Try direct parse first
    try:
        return _json.loads(raw)
    except _json.JSONDecodeError:
        pass
    
    # Fix smart quotes
    raw = raw.replace("\u2018", "'")
    raw = raw.replace("\u2019", "'")
    raw = raw.replace("\u201c", '"')
    raw = raw.replace("\u201d", '"')
    
    # Extract just the JSON object
    match = _re.search(
        r'\{.*\}', raw, _re.DOTALL
    )
    if match:
        try:
            return _json.loads(match.group())
        except _json.JSONDecodeError:
            pass
    
    # Last resort — return safe fallback
    logger.warning(
        "json_parse_failed raw=%s",
        repr(raw[:100])
    )
    return {
        "intent": "normal_chat",
        "reply": (
            "I'm here to help with "
            "anything related to Frostrek. "
            "What would you like to know?"
        )
    }
def normalize_email(text: str) -> str:
    """Convert spoken email words to symbols."""
    text = text.lower()
    text = text.replace(" at ", "@")
    text = text.replace(" dot ", ".")
    text = text.replace(" underscore ", "_")
    text = text.replace(" dash ", "-")
    text = text.replace(" hyphen ", "-")
    return text.strip()


def spell_email(email: str) -> str:
    """Spell email local part letter-by-letter for voice confirmation."""
    if "@" not in email:
        return " ".join(ch.upper() for ch in email)
    local, domain = email.split("@", 1)
    parts = domain.split(".", 1)
    domain_name = parts[0] if parts else domain
    tld = parts[1] if len(parts) > 1 else ""
    spelled_local = " ".join(ch.upper() if ch.isalpha() else ch for ch in local)
    result = f"{spelled_local} at {domain_name}"
    if tld:
        result += f" dot {tld}"
    return result


class IntentExtractionSchema(BaseModel):
    intent: str
    reply: str
    meeting_date: Optional[str] = None
    meeting_time: Optional[str] = None
    meeting_title: Optional[str] = None
    participant_name: Optional[str] = None
    email_address: Optional[str] = None
    email_subject: Optional[str] = None
    email_body: Optional[str] = None


def extract_email_node(state: State) -> State:
    """
    LangGraph node:
    - Reads current session_id and user message
    - Extracts email if present
    - Writes session_id, message, and extracted_email back into state
    """

    session = state.get("session") or {}
    input_block = state.get("input") or {}

    session_id = cast(str, session.get("session_id") or "")
    message = cast(str, input_block.get("normalized_text") or input_block.get("raw_message") or "")

    match = EMAIL_REGEX.search(message)
    extracted_email = match.group(0) if match else None

    if "intent" not in state or state["intent"] is None:
        state["intent"] = {}  # type: ignore[assignment]

    intent = cast(dict, state["intent"])
    if "email" not in intent or intent["email"] is None:
        intent["email"] = {}  # type: ignore[assignment]

    email_block = cast(dict, intent["email"])

    if "session" not in state or state["session"] is None:
        state["session"] = {}  # type: ignore[assignment]
    state["session"]["session_id"] = session_id  # type: ignore[index]

    if "scratch" not in state or state["scratch"] is None:
        state["scratch"] = {}  # type: ignore[assignment]
    state["scratch"]["last_message"] = message  # type: ignore[index]

    email_block["email_address"] = extracted_email
    state["scratch"]["extracted_email"] = extracted_email  # type: ignore[index]

    return state

def format_email_for_speech(email: str) -> str:
    """
    Format email for natural TTS reading.
    Splits into pronounceable chunks with
    punctuation pauses. No slow TTS needed.
    """
    if not email:
        return ""
    
    email = email.lower().strip()
    
    # Split into local part and domain
    if "@" not in email:
        return f"The email is: {email}. Is that correct?"
    
    local, domain = email.split("@", 1)
    
    # Format local part — insert spaces 
    # between letter groups and numbers
    # so TTS reads them as separate chunks
    import re
    
    # Split local by dots
    local_parts = local.split(".")
    spoken_local_parts = []
    
    for part in local_parts:
        # Separate letters from numbers
        segments = re.findall(
            r'[a-zA-Z]+|\d+', part
        )
        spoken_local_parts.append(
            ", ".join(segments)
        )
    
    spoken_local = ", dot, ".join(
        spoken_local_parts
    )
    
    # Format domain
    domain_parts = domain.split(".")
    spoken_domain = ", dot, ".join(
        domain_parts
    )
    
    return (
        f"The email address is: "
        f"{spoken_local}, "
        f"at, {spoken_domain}. "
        f"Is that correct? "
        f"Please say yes or no."
    )

import re

def extract_email_from_text(text: str) -> str | None:
    
    # Step 0 — Normalize spoken email words to symbols
    text = normalize_email(text)
    
    # Step 1 — Try direct regex on original text
    direct = re.findall(
        r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}',
        text
    )
    if direct:
        return min(direct, key=len).lower()
    
    # Step 2 — Clean spoken patterns WITHOUT 
    # removing all spaces (that was the bug)
    spoken = text.lower()
    
    # Replace spoken @ symbols in context
    spoken = re.sub(r'\bat the rate\b', '@', spoken)
    spoken = re.sub(r'\bat rate\b', '@', spoken)
    
    # Remove noise words before replacing spaces
    spoken = spoken.replace("i.e.", "")
    spoken = spoken.replace("i.e", "")
    spoken = spoken.replace("that is", "")
    spoken = spoken.replace("which is", "")
    spoken = spoken.replace("e.g.", "")
    
    # Replace spoken dots only around @ symbol area
    spoken = re.sub(r'\s+dot\s+', '.', spoken)
    spoken = spoken.replace("dot com", ".com")
    spoken = spoken.replace("dot in", ".in")
    spoken = spoken.replace("dot net", ".net")
    spoken = spoken.replace("dot org", ".org")
    spoken = spoken.replace("dot co", ".co")
    
    # Step 3 — Split and find token containing @
    tokens = spoken.split()
    for token in tokens:
        token = token.strip(".,;:()")
        if "@" in token:
            email_match = re.match(
                r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$',
                token
            )
            if email_match:
                return token.lower()
    
    # Step 4 — Handle "ayush @ gmail . com" 
    # with spaces around @
    for i, token in enumerate(tokens):
        if token == "@" and 0 < i < len(tokens)-1:
            candidate = (
                tokens[i-1].strip(".,;:()") +
                "@" +
                tokens[i+1].strip(".,;:()")
            )
            if re.match(
                r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$',
                candidate
            ):
                return candidate.lower()
    
    return None


# ---- Letter collection utilities ----

LETTER_MAP = {
    "alpha": "a", "ay": "a",
    "bravo": "b", "bee": "b",
    "charlie": "c", "sea": "c", "see": "c",
    "delta": "d", "dee": "d",
    "echo": "e", "ee": "e",
    "foxtrot": "f", "ef": "f",
    "golf": "g", "gee": "g",
    "hotel": "h", "aitch": "h",
    "india": "i", "eye": "i",
    "juliet": "j", "jay": "j",
    "kilo": "k", "kay": "k",
    "lima": "l", "el": "l",
    "mike": "m", "em": "m",
    "november": "n", "en": "n",
    "oscar": "o", "oh": "o",
    "papa": "p", "pee": "p",
    "quebec": "q", "cue": "q",
    "romeo": "r", "ar": "r",
    "sierra": "s", "es": "s",
    "tango": "t", "tee": "t",
    "uniform": "u", "you": "u",
    "victor": "v", "vee": "v",
    "whiskey": "w", "double you": "w",
    "x-ray": "x", "ex": "x",
    "yankee": "y", "why": "y",
    "zulu": "z", "zee": "z", "zed": "z",
    "zero": "0", "one": "1", "two": "2",
    "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8",
    "nine": "9",
    "at": "@", "at sign": "@",
    "at the rate": "@",
    "underscore": "_",
    "dash": "-", "hyphen": "-",
    "dot": ".", "period": ".",
    "plus": "+",
}


def extract_email_with_llm(
    text: str,
    state: dict = None
) -> str | None:
    """
    Use LLM to extract email from spoken text.
    Handles accents, variations, and natural
    speech patterns. Returns None if no valid
    email found.
    """
    if not text or not text.strip():
        return None
    
    try:
        import json as _json
        
        tenant_id = state.get("tenant_id", "default") if state else "default"
        llm = get_llm(
            tenant_id=tenant_id
        )
        
        messages = [
            SystemMessage(content=(
                "You extract email addresses "
                "from spoken text.\n\n"
                "Rules:\n"
                "- 'at' or 'at the rate' = @\n"
                "- 'dot' = .\n"
                "- Letters may be spelled out\n"
                "- Numbers may be words:\n"
                "  zero=0 one=1 two=2 three=3\n"
                "  four=4 five=5 six=6 seven=7\n"
                "  eight=8 nine=9\n"
                "- 'ex' or 'x-ray' = x\n"
                "- Common domains: gmail.com\n"
                "  yahoo.com outlook.com\n"
                "  hotmail.com\n\n"
                "ONLY return this JSON:\n"
                '{"email": "found@email.com"}\n'
                "or if not found:\n"
                '{"email": null}\n\n'
                "NEVER add explanation."
                " ONLY JSON."
            )),
            HumanMessage(content=text)
        ]
        
        response = llm.invoke(messages)
        raw = getattr(
            response, "content", ""
        ) or ""
        raw = raw.strip()
        raw = raw.replace("```json", "")
        raw = raw.replace("```", "")
        raw = raw.strip()
        
        parsed = _json.loads(raw)
        email = parsed.get("email")
        
        if not email:
            return None
        
        email = str(email).lower().strip()
        
        # Strict validation
        match = re.match(
            r'^[a-zA-Z0-9._%+\-]+@'
            r'[a-zA-Z0-9.\-]+'
            r'\.[a-zA-Z]{2,}$',
            email
        )
        if match:
            logger.info(
                "llm_email_extraction_success"
                " email=%s",
                email
            )
            return email
        
        logger.warning(
            "llm_email_invalid_format: %s",
            email
        )
        return None
    
    except Exception as e:
        logger.warning(
            "llm_email_extraction_failed: %s",
            e
        )
        return None


def parse_spoken_letter(
    text: str
) -> str | None:
    """
    Convert a spoken word or single character
    into its actual letter/symbol.
    Returns None if not recognized.
    """
    if not text:
        return None
    
    cleaned = text.lower().strip()
    if cleaned == "@":
        return "@"
    if cleaned == ".":
        return "."
        
    cleaned = cleaned.replace(",", "")
    cleaned = cleaned.replace(".", "")
    cleaned = cleaned.replace("!", "")
    cleaned = cleaned.strip()
    
    # Direct single character
    if len(cleaned) == 1 and (
        cleaned.isalpha() or
        cleaned.isdigit()
    ):
        return cleaned
    
    # Check letter map
    if cleaned in LETTER_MAP:
        return LETTER_MAP[cleaned]
    
    # Raw digit string
    if cleaned.isdigit():
        return cleaned
    
    return None


def is_done_signal(text: str) -> bool:
    """User signals they finished spelling."""
    done_words = [
        "done", "finish", "finished",
        "complete", "completed", "end",
        "that's it", "thats it",
        "that is it", "submit",
        "okay done", "ok done",
    ]
    cleaned = text.lower().strip()
    return any(
        w in cleaned for w in done_words
    )


def is_correction_signal(text: str) -> bool:
    """User wants to restart letter input."""
    words = [
        "correction", "wrong", "mistake",
        "delete", "backspace", "clear",
        "start over", "restart", "redo",
        "again", "no that's wrong",
        "not right", "incorrect"
    ]
    cleaned = text.lower().strip()
    return any(w in cleaned for w in words)


def build_email_from_letters(
    letters: list
) -> str:
    """
    Join collected letters into email string.
    Returns empty string if invalid.
    """
    if not letters:
        return ""
    
    email = "".join(letters)
    
    # Must have @ and domain dot
    if "@" not in email:
        return ""
    parts = email.split("@")
    if len(parts) != 2:
        return ""
    if "." not in parts[1]:
        return ""
    if not parts[0]:
        return ""
    
    # Strict regex validation
    match = re.match(
        r'^[a-zA-Z0-9._%+\-]+@'
        r'[a-zA-Z0-9.\-]+'
        r'\.[a-zA-Z]{2,}$',
        email
    )
    return email if match else ""


YES_PHRASES = [
    "yes", "yeah", "yep", "sure", "okay", "ok", 
    "confirm", "confirmed", "correct", "go ahead",
    "send it", "send", "do it", "absolutely", 
    "of course", "right", "affirmative", "yes yes",
    "yes please", "please send", "that's correct",
    "thats correct", "looks good", "good"
]

NO_PHRASES = [
    "no", "nope", "don't", "dont", "cancel", 
    "stop", "decline", "negative", "not", 
    "no thanks", "no thank you", "skip"
]

def detect_confirmation(text: str) -> str:
    cleaned = text.lower().strip()
    cleaned = cleaned.replace(",", "").replace(".", "")
    cleaned = cleaned.replace("!", "").replace("?", "")
    
    for phrase in YES_PHRASES:
        if phrase in cleaned:
            return "yes"
    
    for phrase in NO_PHRASES:
        if phrase in cleaned:
            return "no"
    
    return "unclear"


def handle_confirmation(state: dict) -> dict:
    """
    Handles yes/no confirmation for email.
    Called from chat_router before graph runs.
    Returns a plain dict with intent and reply.
    """
    raw_message = (
        state.get("input", {})
            .get("raw_message", "")
            .strip()
    )

    user_response = detect_confirmation(raw_message)

    email_stage = (
        state.get("intent", {})
            .get("email_stage", "")
    )

    recipient = (
        state.get("recipient") or
        state.get("intent", {})
            .get("email_address") or
        state.get("intent", {})
            .get("email", {})
            .get("email_address") or
        ""
    )

    subject = (
        state.get("subject") or
        state.get("intent", {})
            .get("email_subject") or
        "Information from Frostrek LLP"
    )

    body = (
        state.get("body") or
        state.get("intent", {})
            .get("email_body") or
        state.get("intent", {})
            .get("email", {})
            .get("email_body") or
        ""
    )

    if user_response == "yes":

        if email_stage == "confirm_email":
            
            pending_action = state.get(
                "intent", {}
            ).get("pending_action", "")
            
            state["intent"][
                "email_stage"
            ] = "confirm_send"
            state["awaiting_confirmation"] = True
            recipient = (
                state.get("recipient") or
                state.get("intent", {})
                .get("email_address", "")
            )
            
            # Different confirmation message
            # for meeting vs email
            if pending_action == "schedule_meeting":
                meeting_date = (
                    state.get("intent", {})
                    .get("meeting_date", "")
                )
                meeting_time = (
                    state.get("intent", {})
                    .get("meeting_time", "")
                )
                # Format date nicely
                try:
                    from datetime import datetime as _dt
                    _d = _dt.strptime(
                        meeting_date, "%Y-%m-%d"
                    )
                    _t = _dt.strptime(
                        meeting_time, "%H:%M"
                    )
                    nice_date = _d.strftime(
                        "%B %d, %Y"
                    )
                    nice_time = _t.strftime(
                        "%I:%M %p"
                    ).lstrip("0")
                except Exception:
                    nice_date = meeting_date
                    nice_time = meeting_time
                
                reply = (
                    f"Perfect! I'll book your meeting "
                    f"on {nice_date} at {nice_time} "
                    f"and send the Google Meet link "
                    f"to {recipient}. Shall I go ahead?"
                )
            else:
                # Get what the user originally
                # wanted — use LLM subject as hint
                subject = state.get(
                    "subject", "information"
                )
                
                # Build natural confirm message
                reply = (
                    f"Perfect! I'll send that over "
                    f"to {recipient} right away. "
                    f"Shall I go ahead?"
                )
            
            from session_store import save_session_state
            save_session_state(
                state["session"]["session_id"],
                state
            )
            
            return {
                "intent": "awaiting_confirmation",
                "reply": reply,
                "continuous": True
            }

        elif email_stage in [
            "confirm_send", "", None
        ]:
            
            # ── MEETING CHECK FIRST ──
            # If pending_action is
            # schedule_meeting, we should
            # book the meeting, NOT send email
            pending_action = state.get(
                "intent", {}
            ).get("pending_action", "")
            
            meeting_date = (
                state.get("intent", {})
                .get("meeting_date") or
                state.get("intent", {})
                .get("meeting", {})
                .get("meeting_date")
            )
            meeting_time = (
                state.get("intent", {})
                .get("meeting_time") or
                state.get("intent", {})
                .get("meeting", {})
                .get("meeting_time")
            )
            attendee_email = (
                state.get("recipient") or
                state.get("intent", {})
                .get("email_address")
            )
            
            if (
                pending_action == "schedule_meeting"
                and meeting_date
                and meeting_time
                and attendee_email
            ):
                logger.info(
                    "handle_confirmation: "
                    "routing to calendar "
                    "date=%s time=%s to=%s",
                    meeting_date,
                    meeting_time,
                    attendee_email
                )
                
                # Clear confirmation state
                state["awaiting_confirmation"] = (
                    False
                )
                state["intent"][
                    "email_stage"
                ] = None
                state["intent"][
                    "pending_action"
                ] = None
                
                # Call calendar tool directly
                try:
                    from tools.calendar_tool import (
                        check_availability,
                        create_meeting
                    )
                    from datetime import datetime
                    
                    # Build datetime strings
                    dt_str = (
                        f"{meeting_date} "
                        f"{meeting_time}"
                    )
                    dt = datetime.strptime(
                        dt_str, "%Y-%m-%d %H:%M"
                    )
                    
                    from datetime import timedelta
                    end_dt = dt + timedelta(hours=1)
                    
                    start_iso = dt.strftime(
                        "%Y-%m-%dT%H:%M:%S"
                    )
                    end_iso = end_dt.strftime(
                        "%Y-%m-%dT%H:%M:%S"
                    )
                    
                    tenant_id = "default"
                    
                    # Check availability
                    avail = check_availability(
                        tenant_id,
                        start_iso,
                        end_iso
                    )
                    
                    if avail.get("available"):
                        # Create meeting
                        result = create_meeting(
                            tenant_id=tenant_id,
                            title=(
                                "Meeting with Frostrek LLP"
                            ),
                            description=(
                                "Meeting scheduled via "
                                "Frosty AI Assistant"
                            ),
                            start_iso=start_iso,
                            end_iso=end_iso,
                            attendee_email=attendee_email
                        )
                        
                        if result.get("success"):
                            meet_link = result.get(
                                "meet_link", ""
                            )
                            event_link = result.get(
                                "event_link", ""
                            )
                            
                            # Format date nicely
                            nice_date = dt.strftime(
                                "%B %d at %I:%M %p"
                            )
                            
                            reply = (
                                f"Your meeting has "
                                f"been scheduled for "
                                f"{nice_date}. "
                            )
                            if meet_link:
                                reply += (
                                    f"Here is your "
                                    f"Google Meet link: "
                                    f"{meet_link}. "
                                )
                            reply += (
                                f"A calendar invite "
                                f"has been sent to "
                                f"{attendee_email}."
                            )
                            
                            logger.info(
                                "meeting_scheduled "
                                "date=%s to=%s "
                                "meet=%s",
                                meeting_date,
                                attendee_email,
                                meet_link
                            )
                            
                            return {
                                "intent": (
                                    "meeting_scheduled"
                                ),
                                "reply": reply,
                                "meet_link": (
                                    meet_link
                                ),
                                "continuous": False
                            }
                        else:
                            return {
                                "intent": (
                                    "normal_chat"
                                ),
                                "reply": (
                                    "I wasn't able "
                                    "to create the "
                                    "meeting right "
                                    "now. Please try "
                                    "again or contact "
                                    "us at "
                                    "info@frostrek.com"
                                ),
                                "continuous": False
                            }
                    else:
                        # Slot is busy
                        conflicts = avail.get(
                            "conflicts", []
                        )
                        
                        from tools.calendar_tool\
                            import find_free_slots
                        
                        slots = find_free_slots(
                            tenant_id,
                            meeting_date,
                            duration=60,
                            num_slots=3
                        )
                        
                        if slots:
                            slot_list = "\n".join([
                                f"{i+1}. "
                                f"{s['start_display']}"
                                f" to "
                                f"{s['end_display']}"
                                for i, s in
                                enumerate(slots)
                            ])
                            return {
                                "intent": (
                                    "schedule_meeting"
                                ),
                                "reply": (
                                    f"That slot is "
                                    f"already taken. "
                                    f"Here are "
                                    f"available times "
                                    f"on that day:\n"
                                    f"{slot_list}\n"
                                    f"Which works "
                                    f"best for you?"
                                ),
                                "continuous": True
                            }
                        else:
                            return {
                                "intent": (
                                    "schedule_meeting"
                                ),
                                "reply": (
                                    "That slot is "
                                    "taken and I "
                                    "couldn't find "
                                    "other openings "
                                    "that day. Would "
                                    "you like to try "
                                    "a different date?"
                                ),
                                "continuous": True
                            }
                
                except Exception as e:
                    logger.error(
                        "meeting_booking_failed: "
                        "%s", e,
                        exc_info=True
                    )
                    return {
                        "intent": "normal_chat",
                        "reply": (
                            "I ran into an issue "
                            "booking the meeting. "
                            "Please contact us at "
                            "info@frostrek.com and "
                            "we'll get it sorted."
                        ),
                        "continuous": False
                    }
            
            # If not a meeting, continue with
            # normal email send below...
            # (existing email send code unchanged)
            if not recipient:
                state["awaiting_confirmation"] = False
                state["intent"][
                    "email_stage"
                ] = "ask_email"
                return {
                    "intent": "awaiting_confirmation",
                    "reply": (
                        "Could you please provide "
                        "your email address?"
                    )
                }
            try:
                from tools.email_tool import (
                    send_email
                )
                send_email(recipient, subject, body)
                logger.info(
                    "email_send_success to=%s",
                    recipient
                )
                state["awaiting_confirmation"] = False
                state["recipient"] = None
                state["subject"] = None
                state["body"] = None
                state["intent"][
                    "email_stage"
                ] = None
                state["intent"][
                    "email_address"
                ] = None
                
                try:
                    intent = state.get("intent", {})
                    notify_email_sent(
                        tenant_id=state.get(
                            "tenant_id", "default"
                        ),
                        name=state.get("recipient"),
                        email=intent.get(
                            "email_address"
                        ),
                        subject=intent.get(
                            "subject", ""
                        ),
                        query=state.get(
                            "last_user_message", ""
                        ),
                        interest=intent.get(
                            "interest", ""
                        ),
                    )
                except Exception as _se:
                    logger.warning(
                        "slack email notify: %s",
                        _se
                    )

                return {
                    "intent": "email_sent",
                    "reply": (
                        f"Done! Your email has been"
                        f" sent to {recipient} "
                        f"successfully. Is there "
                        f"anything else I can help "
                        f"you with?"
                    )
                }
            except Exception as e:
                logger.error(
                    "email_send_failed: %s", e
                )
                return {
                    "intent": "error",
                    "reply": (
                        "I'm sorry, there was an "
                        "error sending the email."
                        " Please try again."
                    )
                }

        else:
            if recipient and body:
                try:
                    from tools.email_tool import (
                        send_email
                    )
                    send_email(
                        recipient, subject, body
                    )
                    state[
                        "awaiting_confirmation"
                    ] = False
                    state["recipient"] = None
                    state["subject"] = None
                    state["body"] = None
                    state["intent"][
                        "email_stage"
                    ] = None
                    return {
                        "intent": "email_sent",
                        "reply": (
                            "Done! Email sent "
                            "successfully."
                        )
                    }
                except Exception as e:
                    logger.error(
                        "email_send_failed: %s", e
                    )
            return {
                "intent": "awaiting_confirmation",
                "reply": (
                    f"Should I send the email to "
                    f"{recipient or 'your address'}"
                    f"? Please say yes or no."
                )
            }

    elif user_response == "no":
        
        if email_stage == "confirm_email":
            # User says email address is wrong
            # at the letter-by-letter confirm step
            # Restart letter collection
            state["awaiting_confirmation"] = False
            state["recipient"] = None
            state["subject"] = None
            state["body"] = None
            state["intent"][
                "email_stage"
            ] = "ask_email"
            state["intent"][
                "email_address"
            ] = None
            state["email_attempts"] = 0
            state["letter_collection"] = {
                "active": True,
                "letters": []
            }
            return {
                "intent": "awaiting_confirmation",
                "reply": (
                    "I apologize for that! "
                    "Let's get the right address."
                    " Please say your email one "
                    "letter at a time. "
                    "Say 'at' for the @ symbol, "
                    "'dot' for a period, "
                    "and 'done' when finished. "
                    "Go ahead!"
                )
            }
        
        elif email_stage in [
            "confirm_send", "", None
        ] and recipient:
            # User says wrong email at 
            # final send confirmation
            state["awaiting_confirmation"] = False
            state["recipient"] = None
            state["subject"] = None
            state["body"] = None
            state["intent"][
                "email_stage"
            ] = "ask_email"
            state["intent"][
                "email_address"
            ] = None
            state["email_attempts"] = 0
            state["letter_collection"] = {
                "active": True,
                "letters": []
            }
            return {
                "intent": "awaiting_confirmation",
                "reply": (
                    "No worries! Let's make sure "
                    "we get the right address. "
                    "Please say your email one "
                    "letter at a time. "
                    "Say 'at' for the @ symbol, "
                    "'dot' for a period, "
                    "and 'done' when finished. "
                    "Go ahead!"
                )
            }
        
        else:
            # No email in progress
            # Full cancellation
            state["awaiting_confirmation"] = False
            state["recipient"] = None
            state["subject"] = None
            state["body"] = None
            state["intent"]["email_stage"] = None
            state["intent"][
                "email_address"
            ] = None
            state["letter_collection"] = {
                "active": False,
                "letters": []
            }
            return {
                "intent": "cancelled",
                "reply": (
                    "No problem at all! "
                    "I have cancelled the email."
                    " Is there anything else "
                    "I can help you with?"
                )
            }

    else:
        # Check if user is correcting
        # the email instead of saying yes/no
        # This handles: "no that's wrong,
        # my email is john@gmail.com"
        
        correction_triggers = [
            "wrong", "incorrect", "not right",
            "that's not", "thats not",
            "change", "different", "other",
            "no my email", "no the email",
            "letter", "spell", "one by one",
            "letter by letter"
        ]
        
        msg_lower = raw_message.lower()
        wants_letter_mode = any(
            w in msg_lower
            for w in [
                "letter", "spell",
                "one by one",
                "letter by letter"
            ]
        )
        
        wants_correction = any(
            w in msg_lower
            for w in correction_triggers
        )
        
        if wants_letter_mode:
            # User explicitly wants letter mode
            state["letter_collection"] = {
                "active": True,
                "letters": []
            }
            state["awaiting_confirmation"] = False
            state["intent"][
                "email_stage"
            ] = "ask_email"
            state["recipient"] = None
            state["intent"][
                "email_address"
            ] = None
            return {
                "intent": "awaiting_confirmation",
                "reply": (
                    "Sure! Let's spell it out. "
                    "Say each letter one at a "
                    "time. Say 'at' for @, "
                    "'dot' for a period, "
                    "'done' when finished."
                )
            }
        
        if wants_correction:
            # User wants to provide new email
            # Reset and go back to ask_email
            state["awaiting_confirmation"] = False
            state["recipient"] = None
            state["subject"] = None
            state["body"] = None
            state["intent"][
                "email_stage"
            ] = "ask_email"
            state["intent"][
                "email_address"
            ] = None
            state["email_attempts"] = 0
            state["letter_collection"] = {
                "active": False,
                "letters": []
            }
            return {
                "intent": "awaiting_confirmation",
                "reply": (
                    "My apologies! Please say "
                    "your correct email address."
                )
            }
        
        # Genuinely unclear yes/no
        result = {
            "intent": "awaiting_confirmation",
            "reply": (
                f"I didn't catch that. "
                f"Should I send the email to "
                f"{recipient or 'your address'}"
                f"? Please say yes or no. "
                f"Or say 'wrong email' if the "
                f"address is incorrect."
            )
        }

    # Safety: never return empty reply
    result = result if isinstance(
        result, dict
    ) else {}
    
    if not result.get("reply", "").strip():
        result["reply"] = (
            "I'm processing your request. "
            "Could you please say that again?"
        )
    
    return result

import json
def classify_intent_node(state: State) -> State:
    input_block = state.get("input") or {}
    session_block = state.get("session") or {}
    session_id = session_block.get("session_id")

    logger.info("classify_intent_node start session_id=%s", session_id)

    raw_message = cast(str, input_block.get("raw_message") or "")
    message = raw_message.strip()
    print(f"[DEBUG][classify_intent] Incoming user message: {repr(message[:200])}")

    if "intent" not in state or state["intent"] is None:
        state["intent"] = {}  # type: ignore[assignment]
    if "model_io" not in state or state["model_io"] is None:
        state["model_io"] = {}  # type: ignore[assignment]
    if "scratch" not in state or state["scratch"] is None:
        state["scratch"] = {}  # type: ignore[assignment]

    intent_block = cast(dict, state["intent"])
    model_io = cast(dict, state["model_io"])
    scratch = cast(dict, state["scratch"])

    from session_store import get_session_history
    from memory.user_profile import get_user_profile
    from memory.summary import get_conversation_summary

    # --- LONG TERM MEMORY ---
    long_term_memory = ""
    
    try:
        # Load user profile (name, email, company etc if stored)
        profile = get_user_profile(session_id) if session_id else {}
        if profile:
            long_term_memory += "\nKNOWN USER PROFILE (from past sessions):"
            if profile.get("email"):
                long_term_memory += f"\n- Email: {profile['email']}"
            if profile.get("name"):
                long_term_memory += f"\n- Name: {profile['name']}"
            if profile.get("company"):
                long_term_memory += f"\n- Company: {profile['company']}"
            if profile.get("phone"):
                long_term_memory += f"\n- Phone: {profile['phone']}"
    except Exception as e:
        logger.warning(f"classify_intent: failed to load user profile: {e}")
    
    try:
        summary = get_conversation_summary(session_id) if session_id else ""
        if summary:
            long_term_memory += f"\n\nPAST CONVERSATION SUMMARY:\n{summary}"
            
        # Load last 10 past conversation turns across ALL sessions
        # Use whichever history function exists in this file
        past_history = get_session_history(session_id) if session_id else []
        if past_history:
            long_term_memory += "\n\nRECENT PAST CONVERSATIONS (last 10 turns):"
            for turn in past_history[-10:]:
                user_msg = turn.get("content", "")[:120] if turn.get("role") == "user" else ""
                bot_msg  = turn.get("content", "")[:120] if turn.get("role") == "assistant" else ""
                if user_msg: long_term_memory += f"\n  User: {user_msg}"
                if bot_msg:  long_term_memory += f"\n  Frosty: {bot_msg}"
    except Exception as e:
        logger.warning(f"classify_intent: failed to load past history: {e}")

    # --- CURRENT SESSION CONTEXT ---
    known_email  = (state.get("intent", {}).get("email_address")
                    or state.get("scratch", {}).get("pending_email")
                    or "")
    known_date   = (state.get("intent", {}).get("meeting_date")
                    or state.get("intent", {}).get("meeting", {}).get("meeting_date")
                    or "")
    known_time   = (state.get("intent", {}).get("meeting_time")
                    or state.get("intent", {}).get("meeting", {}).get("meeting_time")
                    or "")
    known_intent = state.get("intent", {}).get("intent") or ""
    known_recipient = state.get("recipient") or ""
    
    current_context = ""
    if any([known_email, known_date, known_time, known_recipient]):
        current_context = "\n\nCURRENT SESSION (already confirmed — do NOT ask again):"
        if known_email or known_recipient:
            current_context += f"\n- User email: {known_email or known_recipient}"
        if known_date:
            current_context += f"\n- Meeting date: {known_date}"
        if known_time:
            current_context += f"\n- Meeting time: {known_time}"
        if known_intent and known_intent not in ("normal_chat", "missing_information"):
            current_context += f"\n- Active task: {known_intent}"
        current_context += (
            "\nNEVER ask the user for their email, date, or time "
            "if it is already listed above."
        )

    from services.bot_config import render_system_prompt
    
    # Context for intent classification
    extra_ctx = {
        "memory_email": known_email,
        "intent_detected": known_intent or "normal_chat"
    }
    SYSTEM_PROMPT = render_system_prompt(tenant_id=state.get("tenant_id", "default"), extra_context=extra_ctx)

    # RAG: retrieve relevant knowledge chunks for the user's message
    rag_context = ""
    try:
        from services.rag_engine import retrieve as rag_retrieve
        rag_context = rag_retrieve(message, top_k=5)
    except Exception as e:
        logger.warning("RAG retrieval failed in classify_intent: %s", e)

    kb_text = f"\nRelevant Knowledge:\n{rag_context}\n" if rag_context else ""

    # Scope restriction is handled by the system prompt, not by code-level checks.
    # The LLM always receives the user message and decides the response.

    from datetime import datetime

    # ---- Build LLM messages for rich intent + entity extraction ----
    extraction_prompt = (
        f"Today's date is {datetime.now().strftime('%B %d, %Y')}.\n\n"
        + SYSTEM_PROMPT + "\n\n"
        "You are also an intent classifier and entity extractor.\n"
        "Analyze the user's message and return a SINGLE JSON object.\n\n"
        "INTENTS — pick EXACTLY ONE:\n"
        '  "schedule_meeting" — user wants to book/schedule a meeting or calendar event\n'
        '  "reschedule_meeting" — user wants to reschedule, move, change time, or cancel/delete an existing meeting\n'
        '  "send_email" — user wants to compose and send an email\n'
        '  "send_email_only" — user provides only email content without needing a chat reply\n'
        '  "missing_information" — user\'s request is clear but lacks required details (date, time, email address, etc.)\n'
        '  "normal_chat" — greetings, general conversation, questions, or anything else\n'
        "\n"
        "RESPONSE FORMAT — return ONLY this JSON, no extra text:\n"
        "{\n"
        '  "intent": "<one of the above>",\n'
        '  "reply": "<your conversational reply as Frosty>",\n'
        '  "meeting_date": "<YYYY-MM-DD or null>",\n'
        '  "meeting_time": "<HH:MM or null>",\n'
        '  "meeting_title": "<string or null>",\n'
        '  "participant_name": "<string or null>",\n'
        '  "email_address": "<string or null>",\n'
        '  "email_subject": "<string or null>",\n'
        '  "email_body": "<string or null>"\n'
        "}\n\n"
        "RULES:\n"
        "- ALWAYS use \"normal_chat\" for questions about Frostrek, greetings, or general conversation.\n"
        "- Answer strictly based on the provided Knowledge Base and System Prompt. If the topic is completely outside the scope of Frostrek, politely decline to answer.\n"
        "- ONLY use \"schedule_meeting\" or \"send_email\" if the user EXPLICITLY asks to book a meeting or send an email. Do not assume these intents from normal questions.\n"
        "- For schedule_meeting: extract date, time, title, participant if mentioned.\n"
        "- For send_email: extract email address, subject, body if mentioned.\n"
        "- For missing_information: USE ONLY if the user has explicitly initiated a scheduling or emailing flow but omitted required fields. Otherwise, just chat normally.\n"
        "- IMPORTANT: The reply field must NEVER be empty. Always provide a helpful conversational response string.\n"
        f"{kb_text}"
    )

    # Append memory layers to system prompt
    enriched_system_prompt = extraction_prompt + long_term_memory + current_context
    
    # Load last 6 turns of THIS session for conversational context
    recent_turns = get_session_history(session_id) if session_id else []
    history_messages = []
    for turn in recent_turns[-6:]:
        if turn.get("role") == "user":
            history_messages.append(
                HumanMessage(content=turn.get("content", ""))
            )
        elif turn.get("role") == "assistant":
            history_messages.append(
                AIMessage(content=turn.get("content", ""))
            )
    
    messages = [
        SystemMessage(content=enriched_system_prompt),
        *history_messages,                    # past turns of this session
        HumanMessage(content=message),        # current message always last
    ]

    # ---- Log: LLM prompt ----
    logger.info("[CLASSIFY] LLM prompt messages (%d total):", len(messages))
    for i, m in enumerate(messages):
        role = type(m).__name__
        content_preview = str(getattr(m, 'content', ''))[:200]
        logger.info("  [%d] %s: %s", i, role, repr(content_preview))

    # ---- Defaults ----
    detected_intent = "normal_chat"
    extracted_reply = ""
    extracted_data = {}

    try:
        llm = get_llm(
            tenant_id=state.get("tenant_id", "default")
        )

        start_time = time.time()
        response = llm.invoke(messages)
        elapsed = time.time() - start_time
        logger.info("LLM response time (classify_intent): %.2f seconds", elapsed)

        response_text = getattr(response, "content", "") or ""
        logger.info("RAW LLM RESPONSE (classify_intent): %s", repr(response_text))
        print(f"[DEBUG][classify_intent] Raw LLM output: {repr(response_text[:300])}")

        # ---- Strict JSON schema validation ----
        parsed = safe_json_parse(response_text)
        
        json_parse_failed = False
        if not isinstance(parsed, dict) or "intent" not in parsed:
            json_parse_failed = True
        elif parsed.get("intent") == "normal_chat" and parsed.get("reply") == "I'm here to help with anything related to Frostrek. What would you like to know?":
            json_parse_failed = True

        if json_parse_failed:
            retry_messages = messages + [
                SystemMessage(content=
                    'CRITICAL: Your last response was not valid JSON. '
                    'You MUST respond with JSON only. '
                    'No plain text. Start with { end with }. '
                    'Format: {"intent": "normal_chat", "reply": "your reply"}'
                )
            ]
            response = llm.invoke(retry_messages)
            response_text = getattr(response, "content", "") or ""
            parsed = safe_json_parse(response_text)

        if not isinstance(parsed, dict) or "intent" not in parsed:
            logger.warning("classify_intent: invalid JSON schema, defaulting to normal_chat")
        else:
            raw_intent = parsed.get("intent", "normal_chat")
            valid_intents = {"schedule_meeting", "reschedule_meeting", "cancel_meeting", "send_email", "send_email_only", "missing_information", "normal_chat"}
            detected_intent = raw_intent if raw_intent in valid_intents else "normal_chat"

            extracted_reply = parsed.get("reply", "") or ""
            extracted_data = {
                "meeting_date": parsed.get("meeting_date"),
                "meeting_time": parsed.get("meeting_time"),
                "meeting_title": parsed.get("meeting_title"),
                "participant_name": parsed.get("participant_name"),
                "email_address": parsed.get("email_address"),
                "email_subject": parsed.get("email_subject"),
                "email_body": parsed.get("email_body"),
            }

            if detected_intent in ["send_email", "send_email_only"]:
                if not extracted_data.get("email_address"):
                    extracted_data["email_address"] = extract_email_from_text(message)
                
                # If we successfully got an email from the first message, bypass "ask_email"
                if extracted_data.get("email_address"):
                    parsed["email_stage"] = "confirm_email"
                    extracted_data["email_stage"] = "confirm_email"

    except json.JSONDecodeError as e:
        logger.warning("classify_intent: JSON parse failed: %s", e)
    except Exception as e:
        logger.error("LLM intent classification failed: %s", e, exc_info=True)

    # ---- Store everything into state ----
    intent_block["intent"] = detected_intent
    intent_block["reply"] = extracted_reply
    for key, val in extracted_data.items():
        if val is not None:
            intent_block[key] = val
            
    if "email_stage" in extracted_data:
        intent_block["email_stage"] = extracted_data["email_stage"]

    # Store meeting sub-block for schedule_meeting_node
    if detected_intent == "schedule_meeting":
        intent_block["meeting"] = {
            "meeting_date": extracted_data.get("meeting_date"),
            "meeting_time": extracted_data.get("meeting_time"),
            "meeting_title": extracted_data.get("meeting_title"),
            "participant_name": extracted_data.get("participant_name"),
        }

    # Store email sub-block for email_flow_node
    if detected_intent in ("send_email", "send_email_only"):
        intent_block["email"] = {
            "email_address": extracted_data.get("email_address"),
            "email_subject": extracted_data.get("email_subject"),
            "email_body": extracted_data.get("email_body"),
        }

    print(f"[DEBUG][classify_intent] Detected intent: {detected_intent}")
    print(f"[DEBUG][classify_intent] Extracted reply (len={len(extracted_reply)}): {repr(extracted_reply[:200])}")
    logger.info("detected_intent=%s reply_len=%s session_id=%s",
                detected_intent, len(extracted_reply), session_id)

    if not extracted_reply or extracted_reply.strip() == "":
        if detected_intent == "other":
            extracted_reply = (
                "I'm not sure I understood that. "
                "Could you rephrase? I can help you "
                "with information about Frostrek or "
                "send you an email with details."
            )
        else:
            extracted_reply = (
                "How can I help you today?"
            )
        intent_block["reply"] = extracted_reply

    # Do NOT pre-write api_response here for normal_chat.
    # Let normal_chat_node always make its own LLM call with full conversation context.
    # Only pre-write for missing_information (which already has a clarifying question).
    if detected_intent == "missing_information" and extracted_reply:
        model_io["api_response"] = {
            "intent": detected_intent,
            "reply": extracted_reply,
        }
        if session_id:
            try:
                raw = extracted_reply.strip()
                raw = raw.replace("```json", "").replace("```", "").strip()
                parsed = json.loads(raw)
                reply = parsed.get("reply", extracted_reply)
            except Exception:
                reply = extracted_reply

            save_message(session_id, "user", message)
            save_message(session_id, "assistant", reply)

    return state


def schedule_meeting_node(state: State) -> State:
    from datetime import datetime, timedelta
    import json as _json
    
    intent_block = cast(
        dict, state.get("intent") or {}
    )
    meeting_block = cast(
        dict, intent_block.get("meeting") or {}
    )
    
    if "model_io" not in state or \
            state["model_io"] is None:
        state["model_io"] = {}
    model_io = cast(dict, state["model_io"])
    
    session_id = state.get(
        "session", {}
    ).get("session_id", "")
    
    # Use "default" tenant for now
    # Will be tenant_id in Phase 2
    tenant_id = state.get(
        "tenant_id", "default"
    )
    
    meeting_date = (
        meeting_block.get("meeting_date") or
        intent_block.get("meeting_date")
    )
    meeting_time = (
        meeting_block.get("meeting_time") or
        intent_block.get("meeting_time")
    )

    # If user gave a new date but no time,
    # do NOT carry over time from a previous
    # turn — ask for the time instead
    meeting_date_raw = intent_block.get("meeting_date") or \
        intent_block.get("meeting", {}).get("meeting_date")

    if meeting_date_raw and not meeting_time:
        return {
            "model_io": {
                "api_response": {
                    "intent": "schedule_meeting",
                    "reply": (
                        f"What time works best "
                        f"for you on that day? "
                        f"We are available "
                        f"between 9:00 AM and "
                        f"6:00 PM."
                    )
                }
            }
        }
    meeting_title = (
        meeting_block.get("meeting_title") or
        intent_block.get("meeting_title") or
        "Meeting with Frostrek LLP"
    )
    participant_name = (
        meeting_block.get("participant_name") or
        intent_block.get("participant_name") or
        "Guest"
    )
    attendee_email = (
        intent_block.get("email_address") or
        state.get("recipient") or
        intent_block.get(
            "email", {}
        ).get("email_address")
    )
    
    # Check calendar connected
    from services.google_auth import (
        is_calendar_connected
    )
    if not is_calendar_connected(tenant_id):
        reply = intent_block.get("reply") or (
            "I'd love to schedule a meeting "
            "for you! However, the calendar "
            "hasn't been connected yet. "
            "Please ask the administrator to "
            "connect the Google Calendar from "
            "the admin panel first."
        )
        model_io["api_response"] = {
            "intent": "schedule_meeting",
            "reply": reply
        }
        return state
    
    # Missing required fields
    if not all([meeting_date, meeting_time]):
        reply = (
            intent_block.get("reply") or
            "To schedule a meeting, I need "
            "the date and time. Could you "
            "please provide those?"
        )
        model_io["api_response"] = {
            "intent": "schedule_meeting",
            "reply": reply
        }
        return state
    
    # Missing attendee email
    if not attendee_email:
        state["intent"][
            "email_stage"
        ] = "ask_email"
        state["awaiting_confirmation"] = False
        state["intent"][
            "pending_action"
        ] = "schedule_meeting"
        model_io["api_response"] = {
            "intent": "ask_email",
            "reply": (
                "I'd be happy to schedule "
                "that meeting! Could you "
                "please provide your email "
                "address so I can send the "
                "invite and meeting link?"
            )
        }
        return state
    
    # Parse datetime
    try:
        date_str = str(meeting_date).strip()
        
        # Fix wrong year from LLM
        # LLM sometimes uses training cutoff
        # year instead of current year
        from datetime import datetime as _dt
        current_year = _dt.now().year
        if date_str.startswith("2024") or \
                date_str.startswith("2023"):
            date_str = date_str.replace(
                date_str[:4],
                str(current_year),
                1
            )
            logger.info(
                "meeting_date_year_corrected"
                " → %s", date_str
            )
        time_str = str(meeting_time).strip()
        
        dt = None
        for fmt in [
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d %I:%M %p",
            "%d-%m-%Y %H:%M",
            "%d/%m/%Y %H:%M",
        ]:
            try:
                dt = datetime.strptime(
                    f"{date_str} {time_str}", fmt
                )
                break
            except ValueError:
                continue
        
        if not dt:
            parsed_date = datetime.strptime(
                date_str, "%Y-%m-%d"
            )
            parsed_time = None
            for tfmt in [
                "%H:%M", "%I:%M %p",
                "%I%p", "%H:%M:%S"
            ]:
                try:
                    parsed_time = datetime.strptime(
                        time_str, tfmt
                    )
                    break
                except ValueError:
                    continue
            
            if parsed_time:
                dt = parsed_date.replace(
                    hour=parsed_time.hour,
                    minute=parsed_time.minute
                )
            else:
                dt = parsed_date
        
        start_iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
        end_iso = (
            dt + timedelta(hours=1)
        ).strftime("%Y-%m-%dT%H:%M:%S")
        
    except Exception as e:
        logger.error(
            "meeting datetime parse failed: %s",
            e
        )
        model_io["api_response"] = {
            "intent": "schedule_meeting",
            "reply": (
                "I had trouble understanding "
                "that date and time. Could you "
                "please say it again? For "
                "example: March 10th at 3 PM."
            )
        }
        return state
    
    # Check availability
    from tools.calendar_tool import (
        check_availability,
        find_free_slots,
        create_meeting
    )
    
    availability = check_availability(
        tenant_id, start_iso, end_iso
    )
    
    if availability.get("error") == \
            "Calendar not connected":
        model_io["api_response"] = {
            "intent": "schedule_meeting",
            "reply": (
                "The calendar isn't connected "
                "yet. Please ask the admin to "
                "connect Google Calendar first."
            )
        }
        return state
    
    if not availability["available"]:
        # Find alternative free slots
        date_only = dt.strftime("%Y-%m-%d")
        free_slots = find_free_slots(
            tenant_id,
            date_only,
            duration_minutes=60,
            num_slots=3
        )
        logger.info(
            "find_free_slots result "
            "date=%s slots=%s",
            date_only,
            free_slots
        )
        
        if free_slots is None:
            free_slots = []
        
        if free_slots:
            slots_text = ""
            for i, slot in enumerate(
                free_slots, 1
            ):
                slots_text += (
                    f"{i}. {slot['start']} "
                    f"to {slot['end']}\n"
                )
            
            conflicts = ", ".join(
                availability["conflicts"][:2]
            )
            # Format date and time nicely
            try:
                from datetime import datetime as _fdt
                _d = _fdt.strptime(
                    meeting_date, "%Y-%m-%d"
                )
                _t = _fdt.strptime(
                    meeting_time, "%H:%M"
                )
                nice_date = _d.strftime(
                    "%B %d, %Y"
                )
                nice_time = _t.strftime(
                    "%I:%M %p"
                ).lstrip("0")
            except Exception:
                nice_date = meeting_date
                nice_time = meeting_time

            reply = (
                f"I'm sorry, the "
                f"{nice_time} slot is "
                f"already occupied "
                f"({conflicts}). "
                f"Here are available slots "
                f"on {nice_date}:\n"
                f"{slots_text}"
                f"Which slot works best "
                f"for you?"
            )
        else:
            reply = (
                f"I'm sorry, the "
                f"{meeting_time} slot on "
                f"{meeting_date} is not "
                f"available and there are "
                f"no other free slots that "
                f"day. Could you suggest "
                f"a different date?"
            )
        
        # Save free slots for follow-up
        state["pending_slots"] = free_slots
        state["pending_meeting"] = {
            "title": meeting_title,
            "attendee_email": attendee_email,
            "participant_name": participant_name,
            "date": date_only
        }
        
        model_io["api_response"] = {
            "intent": "schedule_meeting",
            "reply": reply
        }
        return state
    
    # Slot available — create meeting
    result = create_meeting(
        tenant_id=tenant_id,
        title=meeting_title,
        description=(
            f"Meeting scheduled via Frosty AI\n"
            f"Participant: {participant_name}\n"
            f"Contact: {attendee_email}"
        ),
        start_iso=start_iso,
        end_iso=end_iso,
        attendee_email=attendee_email
    )
    
    if result["success"]:
        meet_link = result.get("meet_link", "")

        # Format date and time nicely
        try:
            from datetime import datetime as _fdt
            _d = _fdt.strptime(
                meeting_date, "%Y-%m-%d"
            )
            _t = _fdt.strptime(
                meeting_time, "%H:%M"
            )
            nice_date = _d.strftime("%B %d, %Y")
            nice_time = _t.strftime(
                "%I:%M %p"
            ).lstrip("0")
        except Exception:
            nice_date = meeting_date
            nice_time = meeting_time

        # Auto-send confirmation email with real link
        try:
            from services.email_service import send_meeting_confirmation
            send_meeting_confirmation(
                to_email=attendee_email,
                participant_name=participant_name,
                meeting_title=meeting_title,
                meeting_time=f"{nice_date} at {nice_time}",
                meet_link=meet_link,
                event_id=result.get("event_id", "")
            )
            logger.info("meeting_confirmation_email_sent to=%s", attendee_email)
        except Exception as e:
            logger.warning("meeting_confirmation_email_failed: %s", e)

        reply = (
            f"Your meeting '{meeting_title}' "
            f"has been scheduled for "
            f"{nice_date} at {nice_time}. "
        )
        if meet_link:
            reply += (
                f"Google Meet link: {meet_link}. "
            )
        reply += (
            f"A confirmation email has been "
            f"sent to {attendee_email}. "
            f"Is there anything else I can help you with?"
        )

        state["awaiting_confirmation"] = False
        state["intent"]["email_stage"] = None
        state["intent"]["pending_action"] = None
        state["recipient"] = None
        
        try:
            notify_meeting_booked(
                tenant_id=state.get(
                    "tenant_id", "default"
                ),
                name=state.get("recipient"),
                email=state.get(
                    "intent", {}
                ).get("email_address"),
                meeting_time=nice_date,
                meet_link=result.get(
                    "meet_link", ""
                ),
                summary=result.get(
                    "summary", ""
                ),
            )
        except Exception as _se:
            logger.warning(
                "slack meeting notify: %s",
                _se
            )
        
        # Clear pending meeting data
        state.pop("pending_slots", None)
        state.pop("pending_meeting", None)
        
        logger.info(
            "meeting_scheduled title=%s "
            "email=%s meet=%s",
            meeting_title,
            attendee_email,
            meet_link
        )
    else:
        reply = (
            "I'm sorry, I couldn't create "
            "the meeting right now. "
            f"Error: {result.get('error')}. "
            "Please try again."
        )
    
    model_io["api_response"] = {
        "intent": "schedule_meeting",
        "reply": reply
    }
    return state


def cancel_meeting_node(state: dict) -> dict:
    """Handle meeting cancellation flow."""
    from tools.calendar_tool import (
        find_meeting_by_session, cancel_meeting
    )
    from services.email_service import send_cancellation_email
    from services.bot_config import get_bot_config
    from config import settings

    session_id = state.get("session", {}).get("session_id", "")
    config = get_bot_config("default")
    admin_email = config.get("contact_email", settings.SMTP_FROM)

    try:
        # Find the meeting for this session
        result = find_meeting_by_session(session_id)

        if not result.get("success"):
            return {
                **state,
                "intent": {
                    "intent": "normal_chat",
                    "reply": "I could not find any upcoming meeting linked to this conversation. If you have a meeting booked, please reach us at " + admin_email + " and we will sort it out."
                }
            }

        event_id = result["event_id"]
        meeting_title = result["title"]
        meeting_time = result["start"]
        attendees = result["attendees"]

        # Cancel on Google Calendar
        cancel_result = cancel_meeting(event_id)

        if not cancel_result.get("success"):
            return {
                **state,
                "intent": {
                    "intent": "normal_chat",
                    "reply": "Something came up while cancelling your meeting. Please reach us directly at " + admin_email + " and we will handle it right away."
                }
            }

        # Get user email from state or attendees
        user_profile = state.get("profile", {})
        user_email = user_profile.get("email", "")
        if not user_email and attendees:
            user_email = attendees[0]
        participant_name = user_profile.get("name", "there")

        # Format meeting time nicely
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(
                meeting_time.replace('Z', '+00:00')
            )
            friendly_time = dt.strftime("%B %d, %Y at %I:%M %p")
        except Exception:
            friendly_time = meeting_time

        # Send cancellation emails
        if user_email:
            send_cancellation_email(
                to_email=user_email,
                participant_name=participant_name,
                meeting_title=meeting_title,
                meeting_time=friendly_time,
                admin_email=admin_email
            )

        return {
            **state,
            "intent": {
                "intent": "cancel_meeting",
                "reply": f"Your meeting has been cancelled successfully. A confirmation has been sent to your email. If you would like to reschedule, just let me know and we will find a new time that works for you."
            }
        }

    except Exception as e:
        print(f"[cancel_meeting_node] Error: {e}")
        return {
            **state,
            "intent": {
                "intent": "normal_chat",
                "reply": "Something came up on our end. Please reach us at " + admin_email + " and we will take care of it."
            }
        }


def email_flow_node(state: State) -> State:
    import json
    
    intent_block = state.get("intent") or {}
    input_block = state.get("input") or {}
    
    if "model_io" not in state or state["model_io"] is None:
        state["model_io"] = {}
    model_io = state["model_io"]

    session_id = state.get("session", {}).get("session_id", "")
    
    email_stage = intent_block.get("email_stage")
    recipient = state.get("recipient") or intent_block.get("email_address")
    raw_message = input_block.get("raw_message", "")
    
    # Only use regex extraction if no email
    # already exists from LLM extraction.
    # LLM extraction is more accurate for voice.
    if not recipient:
        extracted = extract_email_from_text(raw_message)
        if extracted:
            recipient = extracted
        
    if "intent" not in state or not isinstance(state["intent"], dict):
        state["intent"] = {}
        
    if not recipient:
        # Clear any stale letter collection
        state["letter_collection"] = {
            "active": False,
            "letters": []
        }
        state["email_attempts"] = 0
        state["intent"][
            "email_stage"
        ] = "ask_email"
        state["awaiting_confirmation"] = False
        
        # Use LLM reply if available,
        # fallback only if empty
        classify_reply = state.get(
            "intent", {}
        ).get("reply", "")
        
        # Detect if text or voice input
        raw = state.get(
            "input", {}
        ).get("raw_message", "")
        is_voice = "@" not in raw and \
                   len(raw.split()) <= 8
        
        if classify_reply and len(
            classify_reply
        ) > 20:
            ask_reply = classify_reply
        elif is_voice:
            ask_reply = (
                "Could you please share your "
                "email address? You can say it "
                "like: john at gmail dot com."
            )
        else:
            ask_reply = (
                "Could you please share your "
                "email address?"
            )
            
        state["intent"]["reply"] = ask_reply
        model_io["api_response"] = {
            "intent": "ask_email",
            "reply": ask_reply,
        }
        
        from session_store import (
            save_session_state
        )
        if session_id:
            save_session_state(session_id, state)
        return state

    # If we have an email address:
    state["recipient"] = recipient
    state["intent"]["email_address"] = recipient

    email_sub = intent_block.get("email", {})
    subject = email_sub.get("email_subject") or intent_block.get("email_subject") or state.get("subject")
    body = email_sub.get("email_body") or intent_block.get("email_body") or state.get("body")

    if not subject or not body:
        try:
            rag_context = ""
            try:
                from services.rag_engine import retrieve as rag_retrieve
                rag_context = rag_retrieve(raw_message, top_k=5)
            except Exception as e:
                logger.warning("RAG retrieval failed: %s", e)
            
            kb_text = f"""
Relevant Knowledge:
{rag_context}
""" if rag_context else ""

            draft_llm = get_llm(
                tenant_id=state.get("tenant_id", "default")
            )
            
            sys_message_content = (
                "You are an email composer for Frostrek LLP.\n"
                "Your ONLY job is to output a raw JSON object. Nothing else.\n\n"
                "{\n"
                '  "subject": "a short professional email subject line",\n'
                '  "body": "a complete professional email body"\n'
                "}\n\n"
                "Rules:\n"
                "- Always address the recipient politely\n"
                "- Sign off every email as: Frosty | AI Assistant, Frostrek LLP\n"
                f"{kb_text}"
            )

            draft_messages = [
                SystemMessage(content=sys_message_content),
                HumanMessage(content=raw_message),
            ]
            draft_response = draft_llm.invoke(draft_messages)
            raw_response = getattr(draft_response, "content", "") or ""
            
            try:
                raw = raw_response.strip()
                raw = raw.replace("```json", "").replace("```", "").strip()
                parsed = json.loads(raw)
                subject = subject or parsed.get("subject", "Message from Frostrek LLP")
                body = body or parsed.get("body", "")
            except Exception:
                subject = subject or "Message from Frostrek LLP"
                body = body or raw_message.strip()

        except Exception:
            subject = subject or "Message from Frostrek LLP"
            body = body or raw_message.strip()

    state["subject"] = subject
    state["body"] = body

    # EMAIL CONFIRMATION STEP: spell email letter-by-letter before asking to send
    state["intent"]["email_stage"] = "confirm_email"
    state["awaiting_confirmation"] = True
    state["scratch"]["pending_email"] = recipient

    pending_action = state.get(
        "intent", {}
    ).get("pending_action", "")
    
    if pending_action == "schedule_meeting":
        action_context = "your meeting"
    else:
        action_context = "sending you the information"
    
    # Text vs voice
    raw = state.get(
        "input", {}
    ).get("raw_message", "")
    is_typed = "@" in raw
    
    if is_typed:
        reply = (
            f"Got it! I'll use "
            f"{recipient} for "
            f"{action_context}. "
            f"Is that correct?"
        )
    else:
        local, domain = recipient.split(
            "@", 1
        )
        spelled = " ".join(
            list(local.upper())
        )
        reply = (
            f"I heard the email as:\n"
            f"{spelled} at {domain}.\n"
            f"Is this correct?"
        )

    from session_store import save_session_state
    if session_id:
        save_session_state(session_id, state)

    state["intent"]["reply"] = reply
    
    model_io["api_response"] = {
        "intent": "awaiting_confirmation",
        "reply": reply,
    }
    return state

def normal_chat_node(state: State) -> State:
    # If classify already gave a good
    # reply, skip second LLM call
    classify_reply = state.get(
        "intent", {}
    ).get("reply", "")
    
    user_message = state.get(
        "input", {}
    ).get("raw_message", "")
    
    _SIMPLE_PATTERNS = [
        "hi", "hello", "hey", "hiya",
        "bye", "goodbye", "thanks",
        "thank you", "ok", "okay",
        "yes", "no", "sure", "great"
    ]
    
    is_simple = (
        user_message.lower().strip()
        .strip("?!.,") in _SIMPLE_PATTERNS
        or len(user_message.split()) <= 3
    )
    
    has_good_reply = (
        classify_reply
        and len(classify_reply) > 30
        and "json" not in classify_reply
        .lower()
    )
    
    if has_good_reply and is_simple:
        logger.info(
            "normal_chat: skipping LLM, "
            "using classify reply len=%d",
            len(classify_reply)
        )
        api_response = {
            "intent": "normal_chat",
            "reply": classify_reply,
        }
        if "model_io" not in state or state["model_io"] is None:
            state["model_io"] = {}  # type: ignore[assignment]
        state["model_io"]["api_response"] = api_response
        state["response"] = api_response
        return state

    input_block = state.get("input") or {}
    session_block = state.get("session") or {}
    intent_block = cast(dict, state.get("intent") or {})

    session_id = cast(str, session_block.get("session_id") or "")
    user_message = cast(str, input_block.get("raw_message") or "").strip()

    if "model_io" not in state or state["model_io"] is None:
        state["model_io"] = {}  # type: ignore[assignment]
    model_io = cast(dict, state["model_io"])

    if not user_message:
        api_response = {
            "intent": "normal_chat",
            "reply": "I didn't receive any message to respond to.",
        }
        model_io["api_response"] = api_response
        state["response"] = api_response
        return state

    from memory.user_profile import get_user_profile, extract_and_save_profile
    from memory.summary import get_conversation_summary, maybe_summarize_and_prune

    # Extract structural profile facts
    if session_id:
        extract_and_save_profile(session_id, user_message)

    # Always make a fresh LLM call — never skip with a pre-generated reply.
    print(f"[DEBUG][normal_chat] Incoming user message: {repr(user_message[:200])}")
    logger.info("[NORMAL_CHAT] user_message=%s", repr(user_message[:200]))

    # ---- No pre-generated reply — make a full LLM call ----
    history = get_session_history(session_id) if session_id else []
    summary = get_conversation_summary(session_id) if session_id else ""
    profile = get_user_profile(session_id) if session_id else {}
    recent = history[-10:]

    from services.bot_config import render_system_prompt
    
    # Retrieve remembered email and current intent
    memory_email = state.get("scratch", {}).get("extracted_email", "")
    intent_detected = state.get("intent", {}).get("intent", "normal_chat")
    
    extra_ctx = {
        "memory_email": memory_email,
        "intent_detected": intent_detected
    }
    SYSTEM_PROMPT = render_system_prompt(tenant_id=state.get("tenant_id", "default"), extra_context=extra_ctx)


    # Initialize messages list with system prompt
    messages = [SystemMessage(content=SYSTEM_PROMPT)]

    # Inject profile
    if profile and any(profile.values()):
        profile_str = "User Profile:\n"
        if profile.get("name"): profile_str += f"Name: {profile.get('name')}\n"
        if profile.get("email"): profile_str += f"Email: {profile.get('email')}\n"
        if profile.get("preferred_language"): profile_str += f"Preferred Language: {profile.get('preferred_language')}\n"
        if profile.get("timezone"): profile_str += f"Timezone: {profile.get('timezone')}\n"
        messages.append(SystemMessage(content=profile_str.strip()))

    # Inject summary
    if summary:
        messages.append(SystemMessage(content=f"Conversation summary:\n{summary}"))

    # Append recent history
    for msg in recent:
        if msg["role"] == "user":
            messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            # Some older messages might be raw JSON; extract just the reply
            raw_response = msg["content"]
            try:
                import json
                cleaned = raw_response.strip()
                if cleaned.startswith("```json"):
                    cleaned = cleaned[7:]
                if cleaned.startswith("```"):
                    cleaned = cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()
                parsed = json.loads(cleaned)
                reply = parsed.get("reply", raw_response)
            except Exception:
                reply = raw_response
                
            messages.append(AIMessage(content=reply))

    messages.append(HumanMessage(content=user_message))
    
    messages.append(
        SystemMessage(content=(
            "CRITICAL: You MUST respond with "
            "valid JSON only. Start with { "
            "and end with }. "
            "No plain text. No exceptions. "
            "Use this format:\n"
            '{"intent": "normal_chat", '
            '"reply": "your response here"}'
        ))
    )

    logger.info("[NORMAL_CHAT] LLM prompt messages (%d total):", len(messages))
    for i, m in enumerate(messages):
        role = type(m).__name__
        content_preview = str(getattr(m, 'content', ''))[:200]
        logger.info("  [%d] %s: %s", i, role, repr(content_preview))

    reply_text = ""
    try:
        llm = get_llm(
            tenant_id=state.get("tenant_id", "default")
        )

        start_time = time.time()
        response = llm.invoke(messages)
        elapsed = time.time() - start_time
        logger.info("LLM response time (normal_chat): %.2f seconds", elapsed)

        raw_content = getattr(response, "content", "") or ""
        logger.info("RAW LLM RESPONSE (normal_chat): %s", repr(raw_content))
        print(f"[DEBUG][normal_chat] Raw LLM output: {repr(raw_content[:300])}")
        
        import json
        import re
        
        # Parse JSON and extract reply string
        try:
            parsed = safe_json_parse(raw_content)
            reply_text = parsed.get("reply", "")
            if not reply_text:
                reply_text = "How can I help you today?"
            intent_val = parsed.get("intent", "normal_chat")
        except (json.JSONDecodeError, Exception):
            # LLM returned plain text, use as-is
            reply_text = raw_content.strip() if raw_content else (
                "How can I help you today?"
            )
            intent_val = "normal_chat"

    except Exception as e:
        logger.error("LLM call failed in normal_chat_node: %s", e, exc_info=True)
        reply_text = "Sorry, I couldn't generate a response right now."
        intent_val = "error"

    # Proactive follow-up after 3+ exchanges
    # Naturally mention bot capabilities
    try:
        history_count = len(
            get_session_history(session_id)
            if session_id else []
        )
        
        # Every 4th message, add a follow-up
        # suggestion about bot features
        # Only for normal conversation
        # Never during email/meeting flows
        current_stage = state.get(
            "intent", {}
        ).get("email_stage", "")
        
        is_in_flow = (
            state.get("awaiting_confirmation")
            or current_stage
            or state.get(
                "letter_collection", {}
            ).get("active")
        )
        
        # Detect if current reply is already
        # handling a specific action so we
        # don't add confusing follow-ups
        reply_lower = reply_text.lower()
        
        is_already_actionable = any(w in reply_lower for w in [
            "schedule", "meeting", "calendar",
            "email", "send", "book", "slot",
            "available", "date", "time",
            "address", "confirm", "yes or no",
            "shall i", "would you like me to",
            "go ahead", "invite"
        ])
        
        if (
            not is_in_flow and
            not is_already_actionable and
            history_count > 0 and
            history_count % 4 == 0 and
            reply_text and
            len(reply_text) > 20
        ):
            # Use LLM to generate a natural
            # contextual follow-up instead of
            # hardcoded strings
            try:
                
                followup_llm = get_llm(
                    tenant_id=state.get("tenant_id", "default")
                )
                
                # Pick one capability to mention
                import random
                capabilities = [
                    "sending a detailed information email",
                    "scheduling a meeting with our team",
                    "booking a demo call",
                ]
                capability = random.choice(
                    capabilities
                )
                
                followup_messages = [
                    SystemMessage(content=(
                        "You are Frosty, AI assistant "
                        "of Frostrek LLP. Add ONE short "
                        "natural follow-up sentence "
                        "(max 15 words) to the end of "
                        "a conversation. Mention that "
                        f"you can help with: {capability}. "
                        "Be warm and professional. "
                        "Do NOT repeat what was just said. "
                        "Output ONLY the follow-up "
                        "sentence, nothing else."
                    )),
                    HumanMessage(content=(
                        f"Current bot reply: {reply_text}"
                    ))
                ]
                
                followup_response = followup_llm.invoke(
                    followup_messages
                )
                followup_text = getattr(
                    followup_response, "content", ""
                ) or ""
                followup_text = followup_text.strip()
                
                if followup_text and \
                        len(followup_text) > 5:
                    reply_text = (
                        reply_text.rstrip(".!") +
                        ". " + followup_text
                    )
            
            except Exception as e:
                logger.warning(
                    "proactive_followup_failed:"
                    " %s", e
                )
    
    except Exception as e:
        logger.warning(
            "proactive_followup_failed: %s",
            e
        )

    api_response = {
        "intent": intent_val,
        "reply": reply_text,
    }

    if session_id:
        save_message(
            session_id, "user",
            user_message
        )
        save_message(
            session_id, "assistant",
            reply_text
        )
        updated_history = get_session_history(
            session_id
        )
        maybe_summarize_and_prune(
            session_id, updated_history
        )

        # ── Passive profile collection ──
        msg_count = len(updated_history)
        profile = get_user_profile(session_id)

        from memory.user_profile import (
            should_ask_name,
            should_ask_email
        )

        is_in_flow = (
            state.get("awaiting_confirmation")
            or state.get(
                "intent", {}
            ).get("email_stage")
            or state.get(
                "letter_collection", {}
            ).get("active")
        )

        if not is_in_flow:
            if should_ask_name(
                session_id, msg_count
            ):
                reply_text += (
                    " By the way, what "
                    "should I call you?"
                )
                logger.info(
                    "asking_name "
                    "session=%s turn=%d",
                    session_id, msg_count
                )
            elif should_ask_email(
                session_id, msg_count
            ):
                reply_text += (
                    " Would you like me "
                    "to send a summary of "
                    "everything we discussed "
                    "to your email for "
                    "reference?"
                )
                logger.info(
                    "asking_email "
                    "session=%s turn=%d",
                    session_id, msg_count
                )

        # Tag all untagged messages with
        # email if we now know it
        if profile.get("email"):
            try:
                from session_store import (
                    get_connection,
                    release_connection
                )
                conn = get_connection()
                cur = conn.cursor()
                cur.execute(
                    """
                    UPDATE conversation_memory
                    SET user_email = %s
                    WHERE session_id = %s
                    AND user_email IS NULL
                    """,
                    (
                        profile["email"],
                        session_id
                    )
                )
                conn.commit()
                cur.close()
                release_connection(conn)
            except Exception as e:
                logger.warning(
                    "email_tag_failed: %s",
                    e
                )

    api_response["reply"] = reply_text
    print(f"[DEBUG][normal_chat] Final response: intent={api_response['intent']}, reply={repr(reply_text[:200])}")
    model_io["api_response"] = api_response
    state["response"] = api_response

    return state


def reschedule_meeting_node(
    state: State
) -> State:
    """
    Handles reschedule_meeting intent.
    1. Finds existing meeting for user
    2. Deletes it from Google Calendar
    3. Asks for new date/time
    OR if new date/time already provided,
    books directly.
    """
    from datetime import datetime, timedelta
    
    intent_block = cast(
        dict, state.get("intent") or {}
    )
    
    if "model_io" not in state or \
            state["model_io"] is None:
        state["model_io"] = {}
    model_io = cast(dict, state["model_io"])
    
    session_id = state.get(
        "session", {}
    ).get("session_id", "")
    
    tenant_id = state.get(
        "tenant_id", "default"
    )
    
    attendee_email = (
        state.get("recipient") or
        intent_block.get("email_address") or
        state.get("intent", {})
        .get("email", {})
        .get("email_address")
    )
    
    # Step 1 — Find existing meeting
    from tools.calendar_tool import (
        find_event_by_attendee,
        delete_event,
        check_availability,
        create_meeting,
        find_free_slots
    )
    
    existing_event = None
    if attendee_email:
        existing_event = find_event_by_attendee(
            tenant_id, attendee_email
        )
    
    # Step 2 — Delete existing if found
    deleted_info = ""
    if existing_event:
        event_id = existing_event.get("id")
        old_start = existing_event.get(
            "start", {}
        ).get("dateTime", "")
        
        # Format old time nicely
        try:
            import pytz
            IST = pytz.timezone("Asia/Kolkata")
            old_dt = datetime.fromisoformat(
                old_start.replace("Z", "+00:00")
            ).astimezone(IST)
            old_nice = old_dt.strftime(
                "%B %d at %I:%M %p"
            )
        except Exception:
            old_nice = old_start
        
        if event_id:
            del_result = delete_event(
                tenant_id, event_id
            )
            if del_result.get("success"):
                deleted_info = (
                    f"I've cancelled your "
                    f"previous meeting "
                    f"({old_nice}). "
                )
                logger.info(
                    "reschedule_deleted "
                    "event_id=%s old=%s",
                    event_id, old_nice
                )
            else:
                logger.warning(
                    "reschedule_delete_failed"
                    " event_id=%s err=%s",
                    event_id,
                    del_result.get("error")
                )
    
    # Step 3 — Check if new date/time given
    new_date = intent_block.get("meeting_date")
    new_time = intent_block.get("meeting_time")
    
    if not new_date or not new_time:
        # Ask for new time
        reply = (
            deleted_info +
            "When would you like to "
            "reschedule? Please provide "
            "a new date and time."
        )
        model_io["api_response"] = {
            "intent": "reschedule_meeting",
            "reply": reply
        }
        # Set pending reschedule flag
        state["intent"][
            "pending_action"
        ] = "reschedule_meeting"
        return state
    
    # Step 4 — Book new slot
    try:
        dt = datetime.strptime(
            f"{new_date} {new_time}",
            "%Y-%m-%d %H:%M"
        )
        end_dt = dt + timedelta(hours=1)
        start_iso = dt.strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        end_iso = end_dt.strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
    except Exception as e:
        logger.error(
            "reschedule datetime parse: %s",
            e
        )
        model_io["api_response"] = {
            "intent": "reschedule_meeting",
            "reply": (
                deleted_info +
                "I had trouble with that "
                "date and time. Could you "
                "say it again? For example: "
                "March 15th at 2 PM."
            )
        }
        return state
    
    # Check availability
    avail = check_availability(
        tenant_id, start_iso, end_iso
    )
    
    if not avail.get("available"):
        slots = find_free_slots(
            tenant_id, new_date,
            duration=60, num_slots=3
        )
        if slots:
            slot_list = "\n".join([
                f"{i+1}. "
                f"{s['start_display']}"
                f" to {s['end_display']}"
                for i, s in enumerate(slots)
            ])
            # Format date nicely
            try:
                from datetime import datetime as _fdt
                _nd = _fdt.strptime(
                    new_date, "%Y-%m-%d"
                )
                nice_new_date = _nd.strftime(
                    "%B %d, %Y"
                )
            except Exception:
                nice_new_date = new_date

            model_io["api_response"] = {
                "intent": "reschedule_meeting",
                "reply": (
                    deleted_info +
                    f"That slot is busy. "
                    f"Here are free times "
                    f"on {nice_new_date}:\n"
                    f"{slot_list}\n"
                    f"Which works for you?"
                )
            }
        else:
            model_io["api_response"] = {
                "intent": "reschedule_meeting",
                "reply": (
                    deleted_info +
                    "That slot is taken and "
                    "I couldn't find openings "
                    "that day. Try another date?"
                )
            }
        return state
    
    # Book new meeting
    if not attendee_email:
        state["intent"][
            "email_stage"
        ] = "ask_email"
        state["intent"][
            "pending_action"
        ] = "reschedule_meeting"
        state["awaiting_confirmation"] = False
        model_io["api_response"] = {
            "intent": "ask_email",
            "reply": (
                deleted_info +
                "Could you share your email "
                "so I can send the new invite?"
            )
        }
        return state
    
    result = create_meeting(
        tenant_id=tenant_id,
        title="Meeting with Frostrek LLP",
        description=(
            "Rescheduled meeting via "
            "Frosty AI Assistant"
        ),
        start_iso=start_iso,
        end_iso=end_iso,
        attendee_email=attendee_email
    )
    
    if result.get("success"):
        meet_link = result.get(
            "meet_link", ""
        )
        nice_dt = dt.strftime(
            "%B %d, %Y at %I:%M %p"
        )
        # Add auto-send confirmation email 
        try:
            from tools.email_tool import send_email
            email_subject = "Your Meeting with Frostrek LLP is Rescheduled"
            email_body = (
                f"Dear {attendee_email},\n\n"
                f"Your meeting with Frostrek LLP has been "
                f"rescheduled for {nice_dt}.\n\n"
            )
            if meet_link:
                email_body += (
                    f"New Google Meet Link: {meet_link}\n\n"
                )
            email_body += (
                f"Please update your calendar.\n\n"
                f"Looking forward to speaking with you!\n\n"
                f"Best regards,\n"
                f"Frosty | AI Assistant\n"
                f"Frostrek LLP"
            )
            send_email(attendee_email, email_subject, email_body)
            logger.info(
                "reschedule_confirmation_email_sent to=%s",
                attendee_email
            )
        except Exception as e:
            logger.warning(
                "reschedule_confirmation_email_failed: %s", e
            )

        reply = (
            deleted_info +
            f"Your meeting has been "
            f"rescheduled for {nice_dt}. "
        )
        if meet_link:
            reply += (
                f"New Google Meet link: {meet_link}. "
            )
        reply += (
            f"A confirmation email has been "
            f"sent to {attendee_email}. "
            f"Is there anything else I can help you with?"
        )
        
        state["awaiting_confirmation"] = False
        state["intent"]["email_stage"] = None
        state["intent"]["pending_action"] = None
        state["recipient"] = None
        
        logger.info(
            "meeting_rescheduled "
            "new=%s to=%s meet=%s",
            nice_dt, attendee_email,
            meet_link
        )
        
        model_io["api_response"] = {
            "intent": "meeting_rescheduled",
            "reply": reply
        }
    else:
        model_io["api_response"] = {
            "intent": "normal_chat",
            "reply": (
                deleted_info +
                "I cancelled the old meeting "
                "but couldn't book the new "
                "one. Please try again or "
                "contact info@frostrek.com"
            )
        }
    
    return state


def _clear_meeting_state(state: dict) -> None:
    """Clear all meeting-related state fields."""
    intent = state.get("intent", {})
    fields_to_clear = [
        "meeting_date", "meeting_time",
        "meeting_title", "participant_name",
        "pending_action", "email_stage",
        "email_address",
    ]
    for f in fields_to_clear:
        intent[f] = None

    if "meeting" in intent:
        intent["meeting"] = {
            "meeting_date": None,
            "meeting_time": None,
            "meeting_title": None,
            "participant_name": None,
        }

    state["awaiting_confirmation"] = False
    state.pop("pending_slots", None)
    state.pop("pending_meeting", None)



