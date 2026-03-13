import re

with open(r"c:\Users\ayush\OneDrive\Desktop\frosty-langgraph\app.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

output_lines = []
skip = False

for i, line in enumerate(lines):
    if i == 297:  # Line: current_state = get_session_state(str(session_id))
        output_lines.append(line)
        skip = True
        
        # Insert our correct unified flow
        output_lines.append("""
        # -----------------------------------
        # LANGGRAPH FLOW (UNCHANGED)
        # -----------------------------------
        initial_state: State = {
            "session": {
                "session_id": str(session_id),
            },
            "input": {
                "raw_message": user_message,
            },
        }

        logger.debug("[ENTRY] /chat session_id=%s message=%s", session_id, repr(user_message[:200]))

        try:
            from main import chat_router
            api_response_raw = chat_router(str(session_id), initial_state, user_message)

            import json
            parsed = json.loads(api_response_raw) if isinstance(api_response_raw, str) else api_response_raw
            
            if not isinstance(parsed, dict):
                parsed = {}

            intent = parsed.get("intent") or "normal_chat"
            reply = parsed.get("reply") or ""

            # If reply is empty and intent is send_email, the email_flow_node failed — recover gracefully
            if not reply and intent == "send_email":
                intent = "awaiting_confirmation"
                reply = (
                    "Sure! I can send you information "
                    "about Frostrek. Could you please "
                    "share your email address?"
                )

            if not reply and intent == "other":
                intent = "normal_chat"  
                reply = (
                    "I'm not sure I understood that. "
                    "I can help you learn about Frostrek "
                    "or send you an email with details. "
                    "What would you like?"
                )

            api_response = {
                "intent": intent,
                "reply": reply,
                "continuous": bool(continuous_mode)
            }

            # Generate TTS audio if requested or if original message was voice
            if voice_response or message_type == "voice":
                reply_text = str(api_response.get("reply") or "")
                if reply_text:
                    from fastapi.responses import Response
                    from tools.tts_tool import text_to_speech
                    # Request TTS bytes
                    audio_bytes_tts = await text_to_speech(reply_text)
                
                    # Log to chat_logs for analytics before returning early
                    try:
                        from tools.chat_logger import log_chat
                        log_chat(str(session_id), "user", user_message, api_response.get("intent", ""))
                        log_chat(str(session_id), "assistant", reply_text, api_response.get("intent", ""))
                    except Exception as e:
                        logger.warning("chat_logging failed: %s", e)
                    
                    logger.debug("[EXIT] /chat session_id=%s intent=%s reply=%s (binary audio)",
                                 session_id, api_response.get("intent"), repr(reply_text[:200]))

                    return Response(
                        content=audio_bytes_tts,
                        media_type="audio/mpeg"
                    )

        except Exception as e:
            logger.error("Unhandled error in chat endpoint for session %s: %s", session_id, e)
            api_response = {
                "intent": "error",
                "reply": "Something went wrong. Please try again.",
            }

        logger.debug("[EXIT] /chat session_id=%s intent=%s reply=%s",
                     session_id,
                     api_response.get("intent"),
                     repr(str(api_response.get("reply", ""))[:200]))

        # Log to chat_logs for analytics
        try:
            from tools.chat_logger import log_chat
            log_chat(str(session_id), "user", user_message, api_response.get("intent", ""))
            log_chat(str(session_id), "assistant", str(api_response.get("reply", "")), api_response.get("intent", ""))
        except Exception as e:
            logger.warning("chat_logging failed: %s", e)

        return JSONResponse(sanitize_response(api_response.get("intent"), api_response.get("reply"), {"continuous": api_response.get("continuous")}))


    except Exception as e:
        logger.error(
            f"chat_endpoint_unhandled_error "
            f"session={session_id} error={e}",
            exc_info=True
        )
        return JSONResponse(
            sanitize_response(
                "normal_chat",
                "Sorry, I'm having trouble "
                "right now. Please try again."
            ),
            status_code=200
        )
""")
        
    if skip and "except Exception as e:" in line and "chat_endpoint_unhandled_error" in (lines[i+1] if i+1 < len(lines) else ""):
       skip = False
       
    if not skip:
        output_lines.append(line)


with open(r"c:\Users\ayush\OneDrive\Desktop\frosty-langgraph\app.py", "w", encoding="utf-8") as f:
    f.writelines(output_lines)
    
print("Rewrite finished.")
