import json
from typing import Dict

from langgraph.graph import StateGraph

from state import State
from logger_config import logger
from nodes import (
    classify_intent_node,
    schedule_meeting_node,
    reschedule_meeting_node,
    email_flow_node,
    normal_chat_node,
    handle_confirmation,
    cancel_meeting_node,
    _clear_meeting_state,        # NEW — imported for cleanup
)


builder = StateGraph(State)

builder.add_node("classify_intent", classify_intent_node)
builder.add_node("schedule_meeting", schedule_meeting_node)
builder.add_node("reschedule_meeting", reschedule_meeting_node)
builder.add_node("email_flow", email_flow_node)
builder.add_node("normal_chat", normal_chat_node)
builder.add_node("cancel_meeting_node", cancel_meeting_node)


def route_by_intent(state: State) -> str:
    intent_block = state.get("intent") or {}
    intent = str(
        intent_block.get("intent", "normal_chat")
    )

    if intent == "cancel_meeting":
        return "cancel_meeting_node"

    known_intents = {
        "schedule_meeting",
        "reschedule_meeting",
        "cancel_meeting",
        "send_email",
        "send_email_only",
        "missing_information",
        "confirmation_reply",
        "normal_chat",
    }
    if intent not in known_intents:
        logger.info(
            "route_by_intent: unknown intent "
            "'%s', mapping to normal_chat",
            intent
        )
        intent = "normal_chat"
    return intent


builder.set_entry_point("classify_intent")

builder.add_conditional_edges(
    "classify_intent",
    route_by_intent,
    {
        "schedule_meeting": "schedule_meeting",
        "reschedule_meeting": "reschedule_meeting",
        "cancel_meeting_node": "cancel_meeting_node",
        "send_email": "email_flow",
        "send_email_only": "email_flow",
        # ── FIX ── missing_information now
        # returns classify reply directly in
        # chat_router — it never reaches the
        # graph. But keep mapping here as
        # safety net.
        "missing_information": "normal_chat",
        "confirmation_reply": "email_flow",
        "normal_chat": "normal_chat",
    },
)

graph = builder.compile()


from session_store import (
    get_session_state,
    save_session_state
)


# ─────────────────────────────────────────
# STALE STATE FIELDS TO STRIP EACH TURN
# These keys must never carry over from a
# previous turn into a new intent cycle.
# ─────────────────────────────────────────
_STALE_KEYS = ["model_io", "response"]

# Intent fields that are safe to preserve
# across turns (they hold meeting context)
_PRESERVE_INTENT_FIELDS = {
    "email_stage",
    "pending_action",
    "meeting_date",
    "meeting_time",
    "meeting_title",
    "participant_name",
    "email_address",
    "meeting",
}


def _should_clear_meeting_context(
    state: dict,
    user_message: str
) -> bool:
    """
    Detect if the user has moved to a new
    topic and the meeting state should be
    cleared. Prevents stale date/time from
    bleeding into unrelated intents.

    Clears if:
    - No active confirmation pending
    - No active email stage
    - User message looks like a fresh request
      unrelated to continuing meeting flow
    """
    if state.get("awaiting_confirmation"):
        return False

    email_stage = state.get(
        "intent", {}
    ).get("email_stage")
    if email_stage and email_stage != "None":
        return False

    if state.get(
        "letter_collection", {}
    ).get("active"):
        return False

    # Has stale meeting data but no active flow
    has_stale_meeting = bool(
        state.get("intent", {}).get(
            "meeting_date"
        )
    )
    if not has_stale_meeting:
        return False

    # Heuristic: if message is a fresh
    # greeting or unrelated query, clear
    fresh_starts = [
        "hi", "hello", "hey", "book",
        "schedule", "meeting", "can i",
        "i want", "i'd like", "i would",
    ]
    msg_lower = user_message.lower().strip()

    # Only clear if it's clearly a new request
    # NOT a date/time continuation like
    # "on 8th march" or "at 3pm"
    continuation_patterns = [
        "on ", "at ", "march", "april",
        "monday", "tuesday", "wednesday",
        "thursday", "friday", "am", "pm",
    ]
    is_continuation = any(
        msg_lower.startswith(p) or
        p in msg_lower
        for p in continuation_patterns
    )

    if is_continuation:
        return False

    return True


def chat_router(
    session_id: str,
    state: State,
    user_message: str
) -> str:
    """
    Main entry router enforcing confirmation
    priority before classification.

    Changes vs original:
    1. Strips stale model_io/response each turn
    2. Clears stale meeting state on new topics
    3. missing_information replies are returned
       directly without a second LLM call
    4. handle_confirmation now receives
       pending_action correctly
    """
    # ── 1. Load persisted state ──
    db_state = get_session_state(session_id)

    # Strip stale output keys so old replies
    # never leak into new turns
    for k in _STALE_KEYS:
        db_state.pop(k, None)

    # Merge DB state on top of initial state
    state.update(db_state)  # type: ignore

    # Force overwrite raw_message — state.update
    # just restored the previous turn's message
    if "input" not in state or not isinstance(
        state["input"], dict
    ):
        state["input"] = {}
    state["input"]["raw_message"] = user_message

    # ── 2. Clear stale meeting state ──
    # when user starts a genuinely new topic
    if _should_clear_meeting_context(
        state, user_message
    ):
        logger.info(
            "clearing_stale_meeting_state "
            "session=%s",
            session_id
        )
        _clear_meeting_state(state)

    logger.debug("STATE AT START: %s", state)

    # ── 2b. Reschedule slot selection ──
    # When bot just offered slot options
    # from reschedule flow and user picks
    # one (e.g. "9 am", "first one",
    # "go for 10"), carry the pending
    # reschedule date from state and book
    # directly without re-classifying.
    last_intent = (
        state.get("intent", {})
        .get("intent", "")
    )
    pending_reschedule_date = (
        state.get("intent", {})
        .get("meeting_date")
    )

    if (
        last_intent == "reschedule_meeting"
        and pending_reschedule_date
        and not state.get("awaiting_confirmation")
    ):
        # Try to extract a time from the
        # user message directly
        import re as _re2
        time_match = _re2.search(
            r'\b(\d{1,2})\s*(?::|\.)?'
            r'(\d{2})?\s*(am|pm)\b',
            user_message,
            _re2.IGNORECASE
        )
        if time_match:
            hour = int(time_match.group(1))
            mins = int(
                time_match.group(2) or 0
            )
            meridiem = (
                time_match.group(3).lower()
            )
            if meridiem == "pm" and hour != 12:
                hour += 12
            elif meridiem == "am" and hour == 12:
                hour = 0
            picked_time = (
                f"{hour:02d}:{mins:02d}"
            )

            # Inject correct date+time
            # into state before routing
            state["intent"][
                "meeting_time"
            ] = picked_time
            # Keep meeting_date as-is
            # (already March 9 in state)

            logger.info(
                "reschedule_slot_picked "
                "date=%s time=%s session=%s",
                pending_reschedule_date,
                picked_time,
                session_id
            )

            from nodes import (
                reschedule_meeting_node
            )
            result = reschedule_meeting_node(
                state
            )
            save_session_state(
                session_id, result
            )
            api_response = (
                result.get("model_io", {})
                .get("api_response", {})
            )
            return json.dumps(
                api_response or {
                    "intent": "normal_chat",
                    "reply": (
                        "I had trouble booking "
                        "that slot. Please try "
                        "again."
                    )
                }
            )

    # ── 3. Confirmation guard FIRST ──
    if state.get("awaiting_confirmation"):
        print("[ROUTER] Handling confirmation directly")
        logger.debug("CONFIRMATION FLOW ACTIVE")

        # ── CONFLICT GUARD ──
        # If email_stage is ask_email but no
        # recipient yet, user still needs to
        # provide email. Don't treat as
        # confirmation — fall through to
        # ask_email handler below.
        current_stage = (
            state.get("intent", {})
            .get("email_stage", "")
        )
        recipient = (
            state.get("recipient") or
            state.get("intent", {}).get(
                "email_address"
            )
        )

        if (
            current_stage == "ask_email" and
            not recipient
        ):
            # Not a confirmation — user needs
            # to provide email. Clear flag
            # and fall through to ask_email.
            state["awaiting_confirmation"] = False
            save_session_state(session_id, state)

        else:
            # Normal confirmation flow
            state["input"]["raw_message"] = (
                user_message
            )
            result = handle_confirmation(state)
            save_session_state(session_id, state)
            return json.dumps(result)

    # ── 4. Active email stage handling ──
    current_email_stage = (
        state.get("intent", {})
        .get("email_stage", "")
    )
    letter_collection = state.get(
        "letter_collection", {}
    )

    # ─────────────────────────────────────
    # STAGE 2 — LETTER BY LETTER COLLECTION
    # ─────────────────────────────────────
    if letter_collection.get("active"):
        from nodes import (
            parse_spoken_letter,
            is_done_signal,
            is_correction_signal,
            build_email_from_letters,
            format_email_for_speech,
            extract_email_from_text,
            email_flow_node
        )

        letters = letter_collection.get(
            "letters", []
        )
        current_display = "".join(letters)

        if is_correction_signal(user_message):
            state["letter_collection"] = {
                "active": True,
                "letters": []
            }
            save_session_state(session_id, state)
            return json.dumps({
                "intent": "awaiting_confirmation",
                "reply": (
                    "No problem, let's start "
                    "over. Say your email one "
                    "letter at a time. "
                    "Say 'at' for @, "
                    "'dot' for a period, "
                    "'done' when finished."
                )
            })

        if is_done_signal(user_message):
            email = build_email_from_letters(
                letters
            )

            if not email:
                state["letter_collection"] = {
                    "active": True,
                    "letters": []
                }
                save_session_state(
                    session_id, state
                )
                return json.dumps({
                    "intent": "awaiting_confirmation",
                    "reply": (
                        f"I have: {current_display}"
                        f" but that doesn't look "
                        f"like a valid email. "
                        f"Let's try again. "
                        f"Say each letter one at "
                        f"a time, 'at' for @, "
                        f"'dot' for period, "
                        f"'done' when finished."
                    )
                })

            state["letter_collection"] = {
                "active": False,
                "letters": []
            }
            state["recipient"] = email
            state["intent"]["email_address"] = email
            state["intent"]["email_stage"] = (
                "confirm_send"
            )
            state["awaiting_confirmation"] = True
            state["email_attempts"] = 0

            result_state = email_flow_node(state)
            save_session_state(
                session_id, result_state
            )
            api_response = (
                result_state.get("model_io", {})
                .get("api_response", {})
            )

            if not api_response:
                spoken = format_email_for_speech(
                    email
                )
                api_response = {
                    "intent": "awaiting_confirmation",
                    "reply": spoken
                }
            return json.dumps(api_response)

        letter = parse_spoken_letter(user_message)

        if letter:
            letters.append(letter)
            state["letter_collection"]["letters"] = (
                letters
            )
            current = "".join(letters)
            save_session_state(session_id, state)
            return json.dumps({
                "intent": "awaiting_confirmation",
                "reply": (
                    f"{letter}. "
                    f"Got it. So far: {current}."
                    f" Continue or say done."
                )
            })

        import re as _re

        raw_tokens = _re.split(
            r'[\s\-,]+', user_message.strip()
        )

        parsed_letters = []
        all_recognized = True

        for token in raw_tokens:
            if not token:
                continue

            if is_done_signal(token):
                letters.extend(parsed_letters)
                state["letter_collection"][
                    "letters"
                ] = letters

                email = build_email_from_letters(
                    letters
                )
                if not email:
                    state["letter_collection"] = {
                        "active": True,
                        "letters": []
                    }
                    save_session_state(
                        session_id, state
                    )
                    return json.dumps({
                        "intent": "awaiting_confirmation",
                        "reply": (
                            f"I have: "
                            f"{''.join(letters)}"
                            f" but that doesn't "
                            f"look valid. Let's "
                            f"try again."
                        )
                    })

                state["letter_collection"] = {
                    "active": False,
                    "letters": []
                }
                state["recipient"] = email
                state["intent"][
                    "email_address"
                ] = email
                state["intent"][
                    "email_stage"
                ] = "confirm_send"
                state["awaiting_confirmation"] = True
                state["email_attempts"] = 0

                from nodes import (
                    email_flow_node,
                    format_email_for_speech
                )
                result_state = email_flow_node(
                    state
                )
                save_session_state(
                    session_id, result_state
                )
                api_response = (
                    result_state.get(
                        "model_io", {}
                    ).get("api_response", {})
                )
                return json.dumps(
                    api_response or {
                        "intent": "awaiting_confirmation",
                        "reply": (
                            format_email_for_speech(
                                email
                            )
                        )
                    }
                )

            parsed = parse_spoken_letter(token)
            if parsed:
                parsed_letters.append(parsed)
            else:
                if "@" in token or (
                    "." in token and
                    len(token) > 3
                ):
                    for ch in token:
                        if ch == "@":
                            parsed_letters.append(
                                "@"
                            )
                        elif ch == ".":
                            parsed_letters.append(
                                "."
                            )
                        elif ch.isalnum():
                            parsed_letters.append(
                                ch.lower()
                            )
                else:
                    all_recognized = False

        if parsed_letters:
            letters.extend(parsed_letters)
            state["letter_collection"][
                "letters"
            ] = letters
            current = "".join(letters)
            save_session_state(session_id, state)

            email = build_email_from_letters(
                letters
            )
            if email:
                state["letter_collection"] = {
                    "active": False,
                    "letters": []
                }
                state["recipient"] = email
                state["intent"][
                    "email_address"
                ] = email
                state["intent"][
                    "email_stage"
                ] = "confirm_send"
                state["awaiting_confirmation"] = True
                state["email_attempts"] = 0

                from nodes import (
                    email_flow_node,
                    format_email_for_speech
                )
                result_state = email_flow_node(
                    state
                )
                save_session_state(
                    session_id, result_state
                )
                api_response = (
                    result_state.get(
                        "model_io", {}
                    ).get("api_response", {})
                )
                return json.dumps(
                    api_response or {
                        "intent": "awaiting_confirmation",
                        "reply": (
                            format_email_for_speech(
                                email
                            )
                        )
                    }
                )

            return json.dumps({
                "intent": "awaiting_confirmation",
                "reply": (
                    f"Got it. So far: {current}. "
                    f"Continue or say done."
                )
            })

        save_session_state(session_id, state)
        return json.dumps({
            "intent": "awaiting_confirmation",
            "reply": (
                f"I couldn't recognize that. "
                f"Current email: "
                f"{current_display or 'nothing'}."
                f" Please say one letter at a "
                f"time. Say 'correction' to "
                f"start over."
            )
        })

    # ─────────────────────────────────────
    # STAGE 1 — LLM EMAIL EXTRACTION
    # ─────────────────────────────────────
    if current_email_stage == "ask_email":
        from nodes import (
            extract_email_with_llm,
            email_flow_node,
            format_email_for_speech
        )

        extracted = extract_email_with_llm(
            user_message, state
        )

        if extracted:
            state["email_attempts"] = 0
            state["letter_collection"] = {
                "active": False,
                "letters": []
            }
            state["recipient"] = extracted
            state["intent"][
                "email_address"
            ] = extracted
            state["intent"][
                "email_stage"
            ] = "confirm_send"
            state["awaiting_confirmation"] = True

            result_state = email_flow_node(state)
            save_session_state(
                session_id, result_state
            )
            api_response = (
                result_state.get("model_io", {})
                .get("api_response", {})
            )

            if not api_response:
                spoken = format_email_for_speech(
                    extracted
                )
                api_response = {
                    "intent": "awaiting_confirmation",
                    "reply": spoken
                }
            return json.dumps(api_response)

        else:
            attempts = (
                state.get("email_attempts", 0) + 1
            )
            state["email_attempts"] = attempts
            state["intent"][
                "email_stage"
            ] = "ask_email"
            state["awaiting_confirmation"] = True

            if attempts >= 2:
                state["letter_collection"] = {
                    "active": True,
                    "letters": []
                }
                save_session_state(
                    session_id, state
                )
                return json.dumps({
                    "intent": "awaiting_confirmation",
                    "reply": (
                        "I'm having trouble "
                        "catching your email. "
                        "Let me help you spell "
                        "it out. Say each letter "
                        "one at a time. "
                        "Say 'at' for the @ "
                        "symbol, 'dot' for a "
                        "period, and 'done' "
                        "when finished. "
                        "Go ahead!"
                    )
                })

            save_session_state(session_id, state)
            return json.dumps({
                "intent": "awaiting_confirmation",
                "reply": (
                    "I didn't quite catch that. "
                    "Could you say your email "
                    "again slowly? For example: "
                    "john, at, gmail, dot, com."
                )
            })

    # ─────────────────────────────────────
    # ── FIX: missing_information shortcut ──
    # If classify already produced a good
    # clarifying reply, return it directly
    # without invoking normal_chat_node.
    # This eliminates the double LLM call
    # that was adding 3-10 seconds of latency.
    # We check AFTER running classify via graph
    # below, so we intercept the result there.
    # ─────────────────────────────────────

    # ── 5. Run LangGraph ──
    print("[ROUTER] Running classify_intent")
    logger.info(
        "[GRAPH] invoking graph with EXACT "
        "input message=%s",
        repr(user_message)
    )
    print(
        f"[DEBUG][chat_router] Invoking graph "
        f"EXACTLY with: {repr(user_message)}"
    )

    result_state = graph.invoke(state)

    # Persist graph modifications
    save_session_state(session_id, result_state)

    # ── FIX: missing_information shortcut ──
    # classify_intent_node already set
    # model_io.api_response for this intent.
    # Don't let normal_chat_node re-run it.
    # The graph already routes it to
    # normal_chat which makes a second LLM
    # call — we intercept the classify reply
    # here when intent is missing_information
    # and the classify reply is substantial.
    intent_after = (
        result_state.get("intent", {})
        .get("intent", "")
    )
    classify_reply = (
        result_state.get("intent", {})
        .get("reply", "")
    )

    api_response = (
        result_state.get("model_io", {})
        .get("api_response", {})
    )

    # For missing_information: prefer the
    # classify reply (already a good question)
    # over the normal_chat reply when classify
    # produced one and it's substantial
    if (
        intent_after == "missing_information" and
        classify_reply and
        len(classify_reply) > 20
    ):
        api_response = {
            "intent": "missing_information",
            "reply": classify_reply,
        }

    if not api_response:
        api_response = {
            "intent": "normal_chat",
            "reply": (
                "I'm sorry, I didn't understand "
                "that. How can I help you?"
            )
        }

    print(
        f"[DEBUG][chat_router] Graph result: "
        f"intent={api_response.get('intent')}, "
        f"reply={repr(str(api_response.get('reply',''))[:200])}"
    )
    logger.debug(
        "[GRAPH] result model_io.api_response=%s",
        repr(str(api_response)[:300])
    )

    return json.dumps(api_response)


if __name__ == "__main__":
    initial_state: State = {
        "session": {
            "session_id": "demo-session-1",
        },
        "input": {
            "raw_message": (
                "I want to schedule a meeting "
                "tomorrow at 3pm."
            ),
        },
    }

    api_response = chat_router(
        "demo-session-1",
        initial_state,
        "I want to schedule a meeting tomorrow "
        "at 3pm."
    )
    logger.info("API Response: %s", api_response)
