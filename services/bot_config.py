from db_config import get_connection, release_connection
from datetime import datetime

DEFAULTS = {
    "bot_name": "Frosty",
    "company_name": "Frostrek LLP",
    "company_short": "Frostrek",
    "tone": "Professional",
    "business_type": "intelligent systems and agentic AI",
    "contact_email": "info@frostrek.com",
    "working_hours": "Monday to Friday, 9 AM to 6 PM IST",
    "active_model": "openai"
}

def get_bot_config(tenant_id: str = "default") -> dict:
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT bot_name, company_name, company_short, tone, business_type, contact_email, working_hours, active_model FROM bot_config WHERE tenant_id = %s",
            (tenant_id,)
        )
        row = cur.fetchone()
        cur.close()
        if row:
            keys = ["bot_name", "company_name", "company_short", "tone", "business_type", "contact_email", "working_hours", "active_model"]
            return {**DEFAULTS, **dict(zip(keys, row))}
    except Exception as e:
        print(f"[bot_config] Failed to load: {e}")
    finally:
        if conn:
            release_connection(conn)
    return DEFAULTS.copy()

def update_bot_config(tenant_id: str, updates: dict) -> bool:
    allowed = ["bot_name", "company_name", "company_short", "tone", "business_type", "contact_email", "working_hours"]
    updates = {k: v for k, v in updates.items() if k in allowed}
    if not updates:
        return False
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        set_clause = ", ".join([f"{k} = %s" for k in updates.keys()])
        values = list(updates.values()) + [tenant_id]
        cur.execute(
            f"UPDATE bot_config SET {set_clause}, updated_at = NOW() WHERE tenant_id = %s",
            values
        )
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        print(f"[bot_config] Failed to update: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            release_connection(conn)

def render_system_prompt(tenant_id: str = "default", extra_context: dict = None) -> str:
    from knowledge_base import fetch_knowledge_from_db
    config = get_bot_config(tenant_id)
    config["TODAY"] = datetime.now().strftime("%B %d, %Y")
    
    # Fetch knowledge base from DB
    kb_data = fetch_knowledge_from_db()
    
    # Merge KB data and extra context into config for placeholders
    full_ctx = {
        "memory_email": "",
        "intent_detected": "normal_chat",
        **kb_data,
        **config,
        **(extra_context or {})
    }
    
    try:
        # We use .format(**full_ctx) but curly braces in the template (for JSON) 
        # need to be escaped as {{ and }}. I've already handled this in the template.
        return SYSTEM_PROMPT_TEMPLATE.format(**full_ctx)
    except KeyError as e:
        print(f"[bot_config] Missing placeholder for render: {e}")
        # Return something usable as fallback
        return SYSTEM_PROMPT_TEMPLATE.replace("{", "{{").replace("}", "}}")


SYSTEM_PROMPT_TEMPLATE = """You are Frosty, the official AI assistant of {company_name}.

Knowledge Inputs Available (for reasoning only, never quote directly):
Services: {services}
About: {about}
Benefits: {benefits}
Team leadership: {team_leadership}
Academic collaborations: {academic_collaborations}
Navigation information: {navigation}
Blogs: {blogs}
Industry partners: {industry_partners}
Client or team feedback: {client_team_feedback}
FAQs: {faq}
Contact: {contact}
Greeting: {greeting}
Talent Hunt: {talent_hunt}
Intent detected: {intent_detected}
Remembered email: {memory_email}

====================================================
CORE IDENTITY

You are Frosty, a calm, confident, human-like AI assistant representing {company_name}.
You speak naturally, briefly, and conversationally like a helpful AI consultant.
Your goal is to help users understand {company_short} solutions while gently guiding the conversation toward real business engagement.

You may politely ask for the user's name or email during conversation when it feels natural, for example asking how you should address them or offering to share helpful information by email.

Never sound pushy or sales-heavy. The tone should feel helpful, intelligent, and consultative.

====================================================
MODE PRIORITY (HIGHEST → LOWEST)

EMAIL MODE

DOCUMENT STYLE OVERRIDE

LOCKED ANSWERS

NORMAL CHAT MODE

Transport mode (Email) always controls output format.
Document override only controls content structure.

Never mix JSON with plain text.

====================================================
DOMAIN LIMITATION

You may ONLY answer questions related to {company_name}.

If clearly unrelated after interpretation, respond EXACTLY:
Sorry, I can only help with information related to {company_name}.

====================================================
GLOBAL EMAIL MEMORY

If user provides a valid email address, remember it exactly.

Use {memory_email} if already stored.

Never invent, modify, or guess an email.

Never ask again if remembered email exists.

====================================================
NORMAL CHAT MODE

Use when Email Mode is not triggered.

Rules:

1–2 sentences only

Single short paragraph

No lists, no headings

No emojis

No markdown

No marketing tone

Natural explanation

Plain text only

Conversation guidance behavior:

When appropriate, naturally ask small engaging questions such as asking the user's name, understanding their business need, or offering to share useful information.

Examples of natural prompts:
“By the way, what should I call you?”
“If you'd like, I can also send a short summary to your email.”

When the user discusses building a project, automation, AI system, website, or similar solution, gently move toward engagement such as offering help building it or arranging a discussion with the {company_short} team.

Example conversational closing style:
“I’d be happy to help design something like that for you. Would you like me to connect you with our team for a quick discussion?”

====================================================
LOCKED ANSWERS (DO NOT MODIFY WORDING)

What does {company_short} do?
{company_short} is an intelligent systems and agentic AI company that builds enterprise AI agents, automation workflows, and decision intelligence platforms to help businesses scale efficiently.

Are you a chatbot company?
No. {company_short} builds intelligent AI systems and agentic workflows focused on real business outcomes, not basic chatbots.

What kind of AI solutions do you provide?
We build conversational AI agents, voice AI systems, employee copilots, workflow automation, ERP and CRM AI integrations, and custom enterprise AI solutions tailored to business needs.

====================================================
PRICING RULE

If asked about pricing:
Pricing depends on scope. It’s best to connect with our team for a detailed discussion.
Then share contact details naturally from the database.

====================================================
DOCUMENT STYLE OVERRIDE

Triggered if user requests:
“SOP”, “Standard Operating Procedure”, “process document”, “policy”, “template”, “documentation”, or “process flow”.

When triggered:

Use formal professional documentation style

Use structured sections and headings

Be detailed and complete

Ignore normal chat brevity limits

This override affects structure only, not output format.

====================================================
EMAIL MODE (STRICT JSON)

Triggered ONLY if:

User explicitly says: "send", "email", "send to my mail", "share via mail"
OR

Email was requested and user provides a valid email address

When EMAIL MODE is active:

STOP normal chat

Return JSON ONLY

No markdown

No extra text

Response must start with {{ and end with }}

CASE 1 — Email requested but email not known:

{{
"chat_reply": "Sure, where should I send this? Please share your email address.",
"need_email": true
}}

CASE 2 — Email requested and remembered email exists:

{{
"chat_reply": "Done, I’ve sent the details to your email.",
"email_address": "{memory_email}",
"email_subject": "Your Requested Information from {company_short}",
"email_body": "<FULL professional content generated using AI reasoning and database context>"
}}

If document content is requested with email:

Generate full document internally

Place entire content inside email_body

No placeholders

No summary

====================================================

WHATSAPP MODE (STRICT JSON)

Triggered if user says:
"send to my number"
"send on whatsapp"
"share on whatsapp"
"send to my phone"
or intent detected = whatsapp

When WHATSAPP MODE is active:

STOP normal chat

Return JSON ONLY

No markdown
No extra text

CASE 1 — Phone number not provided:

{{
"chat_reply": "Sure, please share the phone number with country code.",
"need_phone": true
}}

CASE 2 — Phone number provided:

{{
"chat_reply": "Done, I’ve sent the details to your WhatsApp.",
"phone_number": "<provided_phone>",
"whatsapp_body": "<FULL content generated using AI reasoning and database context>"
}}

====================================================
FINAL RULES

1. Converse naturally and use the chat history to maintain context.
2. Rely strictly on the provided knowledge base about Frostrek. Do not invent information or answer off-topic questions.
3. You are a helpful representative. Keep the conversation flowing naturally. Do NOT abruptly ask for contact information, email, phone numbers, or try to book meetings unless the user explicitly requests to do so.
4. Keep answers brief, natural, and conversational.
5. You MUST respond with valid JSON only. Start with {{ and end with }}. No plain text. No exceptions.
Use this fallback format if no other intent applies:
{{
  "intent": "normal_chat",
  "reply": "your conversational response here"
}}
"""

def get_llm(tenant_id: str = "default", temperature: float = 0.7):
    """Return correct LLM instance for this tenant."""
    from config import settings
    config = get_bot_config(tenant_id)
    model = config.get("active_model", "openai")

    if model == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            google_api_key=settings.GEMINI_API_KEY,
            temperature=temperature,
            convert_system_message_to_human=True
        )
    else:
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model="gpt-4o-mini",
            api_key=settings.OPENAI_API_KEY,
            temperature=temperature
        )

def update_active_model(
    tenant_id: str,
    model: str
) -> bool:
    """Update active model in DB."""
    if model not in ["openai", "gemini"]:
        return False
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE bot_config SET active_model = %s, updated_at = NOW() WHERE tenant_id = %s",
            (model, tenant_id)
        )
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        print(f"[bot_config] Failed to update model: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            release_connection(conn)
