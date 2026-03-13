def handle_confirmation(state: dict) -> dict:
    """
    Handles yes/no confirmation for both
    email sending AND meeting booking.
    Called from chat_router before graph runs.
    Returns a plain dict with intent and reply.

    FIX SUMMARY:
    - confirm_email stage now checks
      pending_action before asking
      "should I send the email?"
    - Meeting flow never enters email send path
    - State is always cleaned up on exit
    - All slot display uses start_display/
      end_display keys from fixed calendar_tool
    """
    raw_message = (
        state.get("input", {})
            .get("raw_message", "")
            .strip()
    )

    user_response = detect_confirmation(
        raw_message
    )

    email_stage = (
        state.get("intent", {})
            .get("email_stage", "")
    )

    # ── Read pending_action early ──
    # Used in BOTH yes and no branches
    pending_action = (
        state.get("intent", {})
            .get("pending_action", "")
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

    # ─────────────────────────────────────
    # HELPER — book meeting directly
    # Extracted so both confirm_email and
    # confirm_send stages can call it
    # ─────────────────────────────────────
    def _book_meeting() -> dict:
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
        participant_name = (
            state.get("intent", {})
            .get("participant_name") or
            state.get("intent", {})
            .get("meeting", {})
            .get("participant_name") or
            ""
        )
        tenant_id = (
            state.get("session", {})
            .get("tenant_id", "default")
        )

        logger.info(
            "handle_confirmation: "
            "routing to calendar "
            "date=%s time=%s to=%s",
            meeting_date,
            meeting_time,
            attendee_email
        )

        # Clear confirmation state now
        state["awaiting_confirmation"] = False
        state["intent"]["email_stage"] = None
        state["intent"]["pending_action"] = None

        if not all(
            [meeting_date, meeting_time,
             attendee_email]
        ):
            return {
                "intent": "schedule_meeting",
                "reply": (
                    "I'm missing some details "
                    "to complete the booking. "
                    "Could you confirm the date,"
                    " time, and your email?"
                ),
                "continuous": True
            }

        try:
            from tools.calendar_tool import (
                check_availability,
                create_meeting,
                find_free_slots
            )
            from datetime import (
                datetime as _dt,
                timedelta
            )

            dt_str = (
                f"{meeting_date} {meeting_time}"
            )
            dt = _dt.strptime(
                dt_str, "%Y-%m-%d %H:%M"
            )
            end_dt = dt + timedelta(hours=1)
            start_iso = dt.strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
            end_iso = end_dt.strftime(
                "%Y-%m-%dT%H:%M:%S"
            )

            avail = check_availability(
                tenant_id, start_iso, end_iso
            )

            if avail.get("available"):
                result = create_meeting(
                    tenant_id=tenant_id,
                    title="Meeting with Frostrek LLP",
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
                    # Human-friendly date format
                    nice_date = dt.strftime(
                        "%A, %B %d at %I:%M %p"
                    ).replace(" 0", " ")

                    first_name = (
                        participant_name
                        .split()[0]
                        if participant_name
                        else ""
                    )
                    greeting = (
                        f"You're all set"
                        f"{', ' + first_name if first_name else ''}!"
                    )

                    reply = (
                        f"{greeting} Your meeting "
                        f"with Frostrek LLP is "
                        f"confirmed for {nice_date}."
                    )
                    if meet_link:
                        reply += (
                            f" Here's your Google "
                            f"Meet link: {meet_link}"
                        )
                    reply += (
                        f" A calendar invite has "
                        f"been sent to "
                        f"{attendee_email}. "
                        f"Looking forward to "
                        f"speaking with you!"
                    )

                    logger.info(
                        "meeting_scheduled "
                        "date=%s email=%s meet=%s",
                        meeting_date,
                        attendee_email,
                        meet_link
                    )

                    return {
                        "intent": "schedule_meeting",
                        "reply": reply,
                        "meet_link": meet_link,
                        "continuous": False
                    }

                else:
                    logger.error(
                        "create_meeting returned "
                        "failure: %s",
                        result.get("error")
                    )
                    return {
                        "intent": "normal_chat",
                        "reply": (
                            "I had trouble securing "
                            "that slot — there may "
                            "be a calendar sync "
                            "issue. Please try again"
                            " or reach us directly "
                            "at info@frostrek.com."
                        ),
                        "continuous": False
                    }

            else:
                # Slot is busy — find alternatives
                slots = find_free_slots(
                    tenant_id,
                    meeting_date,
                    duration_minutes=60,
                    num_slots=3
                )

                if slots:
                    slot_lines = []
                    for i, s in enumerate(
                        slots, 1
                    ):
                        # Use display keys added
                        # in fixed calendar_tool
                        start_d = s.get(
                            "start_display",
                            s.get("start", "")
                        )
                        end_d = s.get(
                            "end_display",
                            s.get("end", "")
                        )
                        slot_lines.append(
                            f"{i}. {start_d} "
                            f"to {end_d}"
                        )
                    slot_text = "\n".join(
                        slot_lines
                    )
                    return {
                        "intent": "schedule_meeting",
                        "reply": (
                            f"That slot is already "
                            f"taken. Here are some "
                            f"open times I found "
                            f"on that day:\n"
                            f"{slot_text}\n"
                            f"Which one works "
                            f"best for you?"
                        ),
                        "continuous": True
                    }
                else:
                    return {
                        "intent": "schedule_meeting",
                        "reply": (
                            "That slot is taken and "
                            "I couldn't find other "
                            "openings that day. "
                            "Would you like to try "
                            "a different date?"
                        ),
                        "continuous": True
                    }

        except Exception as e:
            logger.error(
                "meeting_booking_failed: %s",
                e, exc_info=True
            )
            # Clean up state on error too
            state["awaiting_confirmation"] = False
            _clear_meeting_state(state)
            return {
                "intent": "normal_chat",
                "reply": (
                    "I ran into a technical issue "
                    "booking that slot. Please try "
                    "again or reach us at "
                    "info@frostrek.com."
                ),
                "continuous": False
            }

    # ─────────────────────────────────────
    # YES BRANCH
    # ─────────────────────────────────────
    if user_response == "yes":

        if email_stage == "confirm_email":
            # ── KEY FIX ──
            # Before asking "should I send
            # the email?", check if we are
            # actually in a meeting flow.
            # If so, skip email confirmation
            # and go straight to booking.
            if pending_action == "schedule_meeting":
                return _book_meeting()

            # Normal email flow — advance stage
            state["intent"]["email_stage"] = (
                "confirm_send"
            )
            state["awaiting_confirmation"] = True
            reply = (
                f"Great! Should I go ahead and "
                f"send the email to {recipient}?"
                f" Say yes or no."
            )
            return {
                "intent": "awaiting_confirmation",
                "reply": reply
            }

        elif email_stage in [
            "confirm_send", "", None
        ]:
            # ── MEETING CHECK ──
            # If pending_action is
            # schedule_meeting, book it.
            if pending_action == "schedule_meeting":
                return _book_meeting()

            # ── EMAIL SEND ──
            if not recipient:
                state["awaiting_confirmation"] = (
                    False
                )
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
                state["awaiting_confirmation"] = (
                    False
                )
                state["recipient"] = None
                state["subject"] = None
                state["body"] = None
                state["intent"][
                    "email_stage"
                ] = None
                state["intent"][
                    "email_address"
                ] = None
                return {
                    "intent": "email_sent",
                    "reply": (
                        f"Done! Your email has "
                        f"been sent to {recipient}"
                        f" successfully. Is there "
                        f"anything else I can help "
                        f"with?"
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
                        "error sending the email. "
                        "Please try again."
                    )
                }

        else:
            # Fallback — try to send if data
            # is present
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

    # ─────────────────────────────────────
    # NO BRANCH
    # ─────────────────────────────────────
    elif user_response == "no":

        # ── If NO during meeting flow ──
        # Cancel meeting booking gracefully
        if pending_action == "schedule_meeting":
            _clear_meeting_state(state)
            state["awaiting_confirmation"] = False
            return {
                "intent": "normal_chat",
                "reply": (
                    "No problem at all! If you'd "
                    "like to book a meeting "
                    "another time, just let me "
                    "know."
                ),
                "continuous": False
            }

        if email_stage == "confirm_email":
            # Email address was wrong
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
                    "Say 'at' for @, 'dot' for a "
                    "period, 'done' when finished."
                    " Go ahead!"
                )
            }

        elif email_stage in [
            "confirm_send", "", None
        ] and recipient:
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
                    "Say 'at' for @, 'dot' for a "
                    "period, 'done' when finished."
                    " Go ahead!"
                )
            }

        else:
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
                    "I've cancelled that. "
                    "Is there anything else "
                    "I can help you with?"
                )
            }

    # ─────────────────────────────────────
    # UNCLEAR BRANCH
    # ─────────────────────────────────────
    else:
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

        # Genuinely unclear — ask again simply
        if pending_action == "schedule_meeting":
            return {
                "intent": "awaiting_confirmation",
                "reply": (
                    "Should I go ahead and book "
                    "that meeting? Please say "
                    "yes or no."
                )
            }

        return {
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


def _clear_meeting_state(state: dict) -> None:
    """
    Clear all meeting-related state fields.
    Call after booking success, failure,
    or cancellation.
    """
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
