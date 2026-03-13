import re

with open(r"c:\Users\ayush\OneDrive\Desktop\frosty-langgraph\app.py", "r", encoding="utf-8") as f:
    content = f.read()

# The redundant confirmation block we want to rip out of app.py
to_remove = """    if current_state.get("awaiting_confirmation"):
        user_response = detect_yes_no(user_message)
        
        email_stage = current_state.get(
            "intent", {}
        ).get("email_stage", "")
        recipient = current_state.get("recipient") or \
            current_state.get(
                "intent", {}
            ).get("email_address", "")
        subject = current_state.get("subject", "")
        body = current_state.get("body", "")
        
        if user_response == "yes":
            if "intent" not in current_state or not isinstance(current_state["intent"], dict):
                current_state["intent"] = {}
                
            if email_stage == "confirm_email":
                # Email address confirmed, now ask 
                # to confirm sending
                current_state["intent"][
                    "email_stage"
                ] = "confirm_send"
                current_state["awaiting_confirmation"] = True
                save_session_state(
                    str(session_id), current_state
                )
                
                recipient = (
                    current_state.get("recipient") or
                    current_state.get("intent", {}).get("email_address") or
                    current_state.get("intent", {}).get("email", {}).get("email_address")
                )
                
                if not recipient:
                    current_state["awaiting_confirmation"] = False
                    current_state["intent"]["email_stage"] = "ask_email"
                    save_session_state(session_id, current_state)
                    reply = (
                        "Could you please provide your "
                        "email address so I can send "
                        "that over to you?"
                    )
                    return JSONResponse(sanitize_response("awaiting_confirmation", reply))
                
                reply = (
                    f"Great! Should I go ahead and "
                    f"send the email to {recipient}? Say yes or no."
                )
                return JSONResponse(sanitize_response("awaiting_confirmation", reply))
            
            elif email_stage == "confirm_send":
                # Send the email now
                try:
                    send_email(recipient, subject, body)
                    current_state["awaiting_confirmation"] = False
                    current_state["recipient"] = None
                    current_state["subject"] = None
                    current_state["body"] = None
                    current_state["intent"][
                        "email_stage"
                    ] = None
                    current_state["intent"][
                        "email_address"
                    ] = None
                    save_session_state(
                        str(session_id), current_state
                    )
                    reply = (
                        "Done! Your email has been sent "
                        "successfully. Is there anything "
                        "else I can help you with?"
                    )
                except Exception as e:
                    logger.error(f"Email send failed: {e}")
                    reply = (
                        "I'm sorry, I encountered an error "
                        "sending the email. Please try again."
                    )
                return JSONResponse(sanitize_response("email_sent", reply))
            
            else:
                # Generic yes during confirmation, 
                # treat as confirm_send
                try:
                    send_email(recipient, subject, body)
                    current_state["awaiting_confirmation"] = False
                    current_state["recipient"] = None
                    current_state["subject"] = None
                    current_state["body"] = None
                    current_state["intent"][
                        "email_stage"
                    ] = None
                    save_session_state(
                        str(session_id), current_state
                    )
                    reply = (
                        "Done! Your email has been sent. "
                        "Is there anything else I can "
                        "help you with?"
                    )
                except Exception as e:
                    reply = "Error sending email. Please try again."
                return JSONResponse(sanitize_response("email_sent", reply))
        
        elif user_response == "no":
            if "intent" not in current_state or not isinstance(current_state["intent"], dict):
                current_state["intent"] = {}
            current_state["awaiting_confirmation"] = False
            current_state["recipient"] = None
            current_state["subject"] = None
            current_state["body"] = None
            current_state["intent"]["email_stage"] = None
            current_state["intent"]["email_address"] = None
            save_session_state(str(session_id), current_state)
            reply = (
                "No problem, I've cancelled the email. "
                "Is there anything else I can help "
                "you with?"
            )
            return JSONResponse(sanitize_response("cancelled", reply))
        
        else:
            # unclear response, ask again
            recipient = (
                current_state.get("recipient") or
                current_state.get("intent", {}).get("email_address") or
                current_state.get("intent", {}).get("email", {}).get("email_address")
            )
            
            if not recipient:
                current_state["awaiting_confirmation"] = False
                current_state["intent"]["email_stage"] = "ask_email"
                save_session_state(session_id, current_state)
                reply = (
                    "Could you please provide your "
                    "email address so I can send "
                    "that over to you?"
                )
                return JSONResponse(sanitize_response("awaiting_confirmation", reply))
                
            reply = (
                f"I didn't catch that. Should I send "
                f"the email to {recipient}? "
                f"Please say yes or no."
            )
            return JSONResponse(sanitize_response("awaiting_confirmation", reply))"""

# If we find this exact chunk we remove it
content = content.replace(to_remove, "")

# Since there is one indentation layer missing from raw to remove block, adjust manually if `replace` missed due to trailing whitespace

lines = content.split('\n')
start = -1
end = -1
for i, line in enumerate(lines):
    if line.strip() == "if current_state.get(\"awaiting_confirmation\"):" and lines[i+1].strip() == "user_response = detect_yes_no(user_message)":
        start = i
    if start != -1 and line.strip() == "return JSONResponse(sanitize_response(\"awaiting_confirmation\", reply))" and lines[i-1].strip() == "f\"Please say yes or no.\"":
        end = i
        break

if start != -1 and end != -1:
    lines = lines[:start] + lines[end+1:]
    content = '\n'.join(lines)
    print(f"Removed redundant app.py lines {start} to {end}")
else:
    print("Could not find indices for fallback removal")


# Write out app.py 
with open(r"c:\Users\ayush\OneDrive\Desktop\frosty-langgraph\app.py", "w", encoding="utf-8") as f:
    f.write(content)
