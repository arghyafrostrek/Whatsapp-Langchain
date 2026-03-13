import codecs
import re

with codecs.open(r"c:\Users\ayush\OneDrive\Desktop\frosty-langgraph\nodes.py", "r", "utf-8") as f:
    content = f.read()

NEW_HANDLE_CONF = """def handle_confirmation(session_id: str, state: State, user_input: str) -> dict:
    from session_store import save_session_state
    
    if isinstance(user_input, dict):
        raw_message = user_input.get("message", "") # type: ignore
    else:
        raw_message = user_input

    msg = str(raw_message).strip().lower()

    if state.get("pending_action_type") == "send_email":
        user_reply = msg

        if user_reply in ["yes", "yes send it", "confirm", "correct", "yep", "sure", "okay", "ok"]:
            from tools.tool_wrapper import execute_with_retry
            from tools.email_tool import send_email
            
            pending_data = state.get("pending_action_data", {})
            execute_with_retry(
                send_email,
                to_email=str(pending_data.get("email", "")),
                subject=str(pending_data.get("subject", "")),
                body=str(pending_data.get("body", "")),
                retries=2,
                timeout=10
            )

            state["action_pending"] = False
            state["pending_action_type"] = None
            state["pending_action_data"] = None
            save_session_state(session_id, state)

            return {
                "intent": "email_sent",
                "reply": "✅ Your email has been sent successfully."
            }

        elif user_reply in ["no", "cancel", "nope"]:
            state["action_pending"] = False
            state["pending_action_type"] = None
            state["pending_action_data"] = None
            save_session_state(session_id, state)

            return {
                "intent": "cancelled",
                "reply": "Okay. How else may I assist you? Would you like to schedule a meeting?"
            }

        else:
            return {
                "intent": "awaiting_confirmation",
                "reply": "Please say yes to confirm sending the email, or no to cancel."
            }
            
    # Fallback if no specific pending action
    return {
        "intent": "error",
        "reply": "I'm not sure what we're confirming."
    }

"""

NEW_EMAIL_FLOW = """def email_flow_node(state: State) -> State:
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import HumanMessage, SystemMessage
    import json
    
    intent_block = state.get("intent") or {}
    input_block = state.get("input") or {}
    raw_message = input_block.get("raw_message", "")
    email_sub = intent_block.get("email", {})

    if "model_io" not in state or state["model_io"] is None:
        state["model_io"] = {}
    model_io = state["model_io"]

    session_id = state.get("session", {}).get("session_id", "")

    # 1. Try LLM-extracted email address first, then our custom extractor
    recipient = email_sub.get("email_address") or intent_block.get("email_address")
    if not recipient:
        recipient = extract_email_from_text(raw_message)

    if not recipient:
        reply = "I couldn't find a valid email address in your message. Could you please provide your email address?"
        
        from session_store import save_session_state
        state["action_pending"] = False
        save_session_state(session_id, state)
            
        api_response = {
            "intent": "send_email",
            "reply": reply,
        }
        model_io["api_response"] = api_response
        return state

    intent_block["email_address"] = recipient

    # 2. Get subject and body
    subject = email_sub.get("email_subject") or intent_block.get("email_subject")
    body = email_sub.get("email_body") or intent_block.get("email_body")

    if not subject or not body:
        # Generate them
        try:
            rag_context = ""
            try:
                from services.rag_engine import retrieve as rag_retrieve
                rag_context = rag_retrieve(raw_message, top_k=5)
            except Exception as e:
                logger.warning("RAG retrieval failed: %s", e)
            
            kb_text = f"\\nRelevant Knowledge:\\n{rag_context}\\n" if rag_context else ""

            draft_llm = ChatOpenAI(
                model="gpt-4o-mini",
                api_key=settings.OPENAI_API_KEY,
                max_tokens=300,
                temperature=0.3,
            )
            
            sys_message_content = (
                "You are an email composer for Frostrek LLP.\\n"
                "Your ONLY job is to output a raw JSON object. Nothing else.\\n\\n"
                "{\\n"
                '  "subject": "a short professional email subject line",\\n'
                '  "body": "a complete professional email body"\\n'
                "}\\n\\n"
                "Rules:\\n"
                "- Always address the recipient politely\\n"
                "- Sign off every email as: Frosty | AI Assistant, Frostrek LLP\\n"
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

    # 3. SET STRICT CONFIRMATION
    state["action_pending"] = True
    state["pending_action_type"] = "send_email"
    state["pending_action_data"] = {
        "email": recipient,
        "subject": subject,
        "body": body
    }

    from session_store import save_session_state
    save_session_state(session_id, state)

    reply = f"Please confirm. Should I send the email to {recipient}? (Yes/No)"

    model_io["api_response"] = {
        "intent": "awaiting_confirmation",
        "reply": reply,
    }
    return state

"""

handle_conf_pattern = re.compile(r'def handle_confirmation\(session_id.*?(?=import json\r?\n\r?\ndef classify_intent_node)', re.DOTALL)
if handle_conf_pattern.search(content):
    content = handle_conf_pattern.sub(NEW_HANDLE_CONF, content)
    print("Replaced handle_confirmation")
else:
    print("Could not find handle_confirmation")

email_flow_pattern = re.compile(r'def email_flow_node\(state: State\) -> State:.*?(?=def normal_chat_node)', re.DOTALL)

if email_flow_pattern.search(content):
    content = email_flow_pattern.sub(NEW_EMAIL_FLOW, content)
    print("Replaced email_flow_node")
else:
    print("Could not find email_flow_node")

with codecs.open(r"c:\Users\ayush\OneDrive\Desktop\frosty-langgraph\nodes.py", "w", "utf-8") as f:
    f.write(content)

print("Done")
