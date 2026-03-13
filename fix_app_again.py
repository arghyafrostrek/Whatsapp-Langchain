import re

with open(r"c:\Users\ayush\OneDrive\Desktop\frosty-langgraph\app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

new_block = """        current_state = get_session_state(str(session_id))
        
        if current_state.get("awaiting_confirmation"):
            from tools.intent_classifier import detect_yes_no
            user_response = detect_yes_no(user_message)
            
            email_stage = (
                current_state.get("intent", {})
                    .get("email_stage") or
                current_state.get("email_stage") or
                ""
            )
            
            recipient = (
                current_state.get("recipient") or
                current_state.get("intent", {})
                    .get("email_address") or
                current_state.get("intent", {})
                    .get("email", {})
                    .get("email_address") or
                ""
            )
            
            subject = (
                current_state.get("subject") or
                current_state.get("intent", {})
                    .get("email_subject") or
                "Information from Frostrek LLP"
            )
            
            body = (
                current_state.get("body") or
                current_state.get("intent", {})
                    .get("email_body") or
                current_state.get("intent", {})
                    .get("email", {})
                    .get("email_body") or
                ""
            )
            
            if user_response == "yes":
                if email_stage == "confirm_email":
                    if "intent" not in current_state or not isinstance(current_state["intent"], dict):
                        current_state["intent"] = {}
                    # Address confirmed, ask to send
                    current_state["intent"][
                        "email_stage"
                    ] = "confirm_send"
                    current_state["awaiting_confirmation"] = True
                    save_session_state(session_id, current_state)
                    reply = (
                        f"Great! Should I go ahead and "
                        f"send the email to {recipient}? "
                        f"Say yes or no."
                    )
                    return JSONResponse(sanitize_response(
                        "awaiting_confirmation", reply
                    ))
                
                elif email_stage in [
                    "confirm_send", "confirm_send_only", ""
                ]:
                    # Send the email NOW
                    if not recipient:
                        if "intent" not in current_state or not isinstance(current_state["intent"], dict):
                            current_state["intent"] = {}
                        current_state["awaiting_confirmation"] = False
                        current_state["intent"]["email_stage"] = "ask_email"
                        save_session_state(session_id, current_state)
                        return JSONResponse(sanitize_response(
                            "awaiting_confirmation",
                            "Could you please provide your "
                            "email address?"
                        ))
                    try:
                        from tools.email_tool import send_email
                        send_email(recipient, subject, body)
                        logger.info(
                            f"email_send_success to={recipient}"
                        )
                        current_state["awaiting_confirmation"] = False
                        current_state["recipient"] = None
                        current_state["subject"] = None
                        current_state["body"] = None
                        if "intent" in current_state and isinstance(current_state["intent"], dict):
                            current_state["intent"]["email_stage"] = None
                            current_state["intent"]["email_address"] = None
                        save_session_state(session_id, current_state)
                        reply = (
                            f"Done! Your email has been sent "
                            f"to {recipient} successfully. "
                            f"Is there anything else I can "
                            f"help you with?"
                        )
                        return JSONResponse(sanitize_response(
                            "email_sent", reply
                        ))
                    except Exception as e:
                        logger.error(f"email_send_failed: {e}")
                        return JSONResponse(sanitize_response(
                            "normal_chat",
                            "I'm sorry, there was an error "
                            "sending the email. Please try again."
                        ))
                
                else:
                    # Unknown stage but user said yes —
                    # treat as confirm_send
                    if "intent" not in current_state or not isinstance(current_state["intent"], dict):
                        current_state["intent"] = {}
                    current_state["intent"][
                        "email_stage"
                    ] = "confirm_send"
                    save_session_state(session_id, current_state)
                    # Recurse by falling into confirm_send 
                    # — just attempt to send
                    if recipient:
                        try:
                            from tools.email_tool import send_email
                            send_email(recipient, subject, body)
                            current_state["awaiting_confirmation"] = False
                            current_state["recipient"] = None
                            current_state["subject"] = None
                            current_state["body"] = None
                            current_state["intent"]["email_stage"] = None
                            save_session_state(session_id, current_state)
                            reply = (
                                "Done! Email sent successfully."
                            )
                            return JSONResponse(sanitize_response(
                                "email_sent", reply
                            ))
                        except Exception as e:
                            pass
                    return JSONResponse(sanitize_response(
                        "awaiting_confirmation",
                        f"Should I send the email to "
                        f"{recipient or 'your address'}? "
                        f"Please say yes or no."
                    ))

            elif user_response == "no":
                current_state["awaiting_confirmation"] = False
                current_state["recipient"] = None
                current_state["subject"] = None
                current_state["body"] = None
                if "intent" in current_state and isinstance(current_state["intent"], dict):
                    current_state["intent"]["email_stage"] = None
                    current_state["intent"]["email_address"] = None
                save_session_state(session_id, current_state)
                return JSONResponse(sanitize_response(
                    "cancelled",
                    "No problem, I've cancelled the email. "
                    "Is there anything else I can help "
                    "you with?"
                ))

            else:
                # Unclear — re-prompt
                return JSONResponse(sanitize_response(
                    "awaiting_confirmation",
                    f"Should I send the email to "
                    f"{recipient}? Please say yes or no."
                ))
"""

output_lines = []
for i, line in enumerate(lines):
    if line.strip() == "current_state = get_session_state(str(session_id))":
        output_lines.append(new_block + "\n")
    else:
        output_lines.append(line)

with open(r"c:\Users\ayush\OneDrive\Desktop\frosty-langgraph\app.py", "w", encoding="utf-8") as f:
    f.writelines(output_lines)
print("Updated app.py with the confirmation logic back in explicitly!")
