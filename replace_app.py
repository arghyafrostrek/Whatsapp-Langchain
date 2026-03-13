import codecs

with codecs.open(r"c:\Users\ayush\OneDrive\Desktop\frosty-langgraph\app.py", "r", "utf-8") as f:
    content = f.read()

# 1. Insert sanitize_response
sanitize_func = '''
def sanitize_response(
    intent: str, 
    reply: str, 
    extra: dict = None
) -> dict:
    """
    Guaranteed response contract for all API consumers. Never returns empty fields.
    """
    FALLBACK_REPLY = (
        "I'm sorry, I didn't quite catch that. "
        "Could you please rephrase your request?"
    )
    
    safe_intent = intent if intent and intent not in ["", "other", "None", None] else "normal_chat"
    safe_reply = reply if reply and reply.strip() != "" else FALLBACK_REPLY
    
    response = {
        "intent": safe_intent,
        "reply": safe_reply,
        "continuous": safe_intent in ["awaiting_confirmation", "ask_email"]
    }
    
    if extra:
        response.update(extra)
    
    return response

@app.post("/chat")
'''
content = content.replace('@app.post("/chat")', sanitize_func)

# 2. Add LangGraph parser fallback logic
old_parse_logic = '''        api_response = chat_router(str(session_id), initial_state, user_message)

        if not isinstance(api_response, dict):
            api_response = {}

        if not api_response.get("intent"):
            api_response["intent"] = "other"
        if not api_response.get("reply"):
            api_response["reply"] = ""
            
        api_response["continuous"] = bool(continuous_mode)'''

new_parse_logic = '''        api_response_raw = chat_router(str(session_id), initial_state, user_message)

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
        }'''
content = content.replace(old_parse_logic, new_parse_logic)

# Replace the final JSONResponse(api_response)
content = content.replace('return JSONResponse(api_response)', 'return JSONResponse(sanitize_response(api_response.get("intent"), api_response.get("reply"), {"continuous": api_response.get("continuous")}))')

# Now replacing standard JSONResponse patterns...
content = content.replace('''return JSONResponse({"error": "Invalid audio format"}, status_code=400)''', '''return JSONResponse(sanitize_response("error", "Invalid audio format", {"error": "Invalid audio format"}), status_code=400)''')
content = content.replace('''return JSONResponse({"error": "Audio file missing"}, status_code=400)''', '''return JSONResponse(sanitize_response("error", "Audio file missing", {"error": "Audio file missing"}), status_code=400)''')
content = content.replace('''return JSONResponse({"error": "Audio processing failed"}, status_code=500)''', '''return JSONResponse(sanitize_response("error", "Audio processing failed", {"error": "Audio processing failed"}), status_code=500)''')
content = content.replace('''return JSONResponse({"error": "Unsupported Content-Type"}, status_code=415)''', '''return JSONResponse(sanitize_response("error", "Unsupported Content-Type", {"error": "Unsupported Content-Type"}), status_code=415)''')
content = content.replace('''return JSONResponse({"error": "Empty message"}, status_code=400)''', '''return JSONResponse(sanitize_response("error", "Empty message", {"error": "Empty message"}), status_code=400)''')

content = content.replace('''        return JSONResponse({
            "intent": "error",
            "reply": "Too many requests. Please slow down.",
        })''', '''        return JSONResponse(sanitize_response("error", "Too many requests. Please slow down."))''')

content = content.replace('''                return JSONResponse({
                    "intent": "awaiting_confirmation",
                    "reply": reply
                })''', '''                return JSONResponse(sanitize_response("awaiting_confirmation", reply))''')

content = content.replace('''                return JSONResponse({
                    "intent": "email_sent",
                    "reply": reply
                })''', '''                return JSONResponse(sanitize_response("email_sent", reply))''')

content = content.replace('''                return JSONResponse({
                    "intent": "email_sent", 
                    "reply": reply
                })''', '''                return JSONResponse(sanitize_response("email_sent", reply))''')

content = content.replace('''            return JSONResponse({
                "intent": "cancelled",
                "reply": reply
            })''', '''            return JSONResponse(sanitize_response("cancelled", reply))''')

content = content.replace('''            return JSONResponse({
                "intent": "awaiting_confirmation",
                "reply": reply
            })''', '''            return JSONResponse(sanitize_response("awaiting_confirmation", reply))''')

with codecs.open(r"c:\Users\ayush\OneDrive\Desktop\frosty-langgraph\app.py", "w", "utf-8") as f:
    f.write(content)

print("Replacement complete.")
