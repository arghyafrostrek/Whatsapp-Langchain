import os
import tempfile
import urllib.parse
from dotenv import load_dotenv

# Ensure .env is loaded before other imports
load_dotenv()

import time
import uuid
from collections import defaultdict
from typing import Any, Dict, Optional
import base64

from fastapi import FastAPI, Request, Header, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from logger_config import logger
from config import settings
from state import State
from main import graph
from db_config import init_conversation_table
from tools.voice_tool import transcribe_audio
from tools.tts_tool import text_to_speech as generate_speech
from session_store import get_session_state, save_session_state
from tools.email_tool import send_email

from routers import whatsapp


def sanitize_text(text: str) -> str:
    """Remove NUL bytes and control chars that PostgreSQL cannot store."""
    if not text:
        return ""
    text = text.replace("\x00", "")
    cleaned = ""
    for char in text:
        code = ord(char)
        if code == 0:
            continue
        elif code < 32 and char not in "\n\r\t":
            continue
        else:
            cleaned += char
    return cleaned.strip()


# ---- Startup environment validation ----

_REQUIRED_ENV_VARS = [
    "OPENAI_API_KEY",
    "DATABASE_URL",
    "SMTP_HOST",
    "SMTP_USER",
    "SMTP_PASS",
]

_missing = [v for v in _REQUIRED_ENV_VARS if not os.getenv(v)]
if _missing:
    raise RuntimeError(
        f"Missing required environment variables: {', '.join(_missing)}. "
        "Please set them in your .env file before starting the server."
    )


app = FastAPI(
    title="Frosty API",
    description="Backend API for Frosty LangGraph Agent with Voice & Google Auth",
    version="1.0.0"
)

app.include_router(whatsapp.router)

# Create conversation_memory table on startup (idempotent)
init_conversation_table()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # temporary for testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- In-memory rate limiter (20 requests/minute per session_id) ----

RATE_LIMIT_MAX = 20
RATE_LIMIT_WINDOW = 60  # seconds

_rate_store: Dict[str, list] = defaultdict(list)


def _is_rate_limited(session_id: str) -> bool:
    """Return True if the session_id has exceeded the rate limit."""
    now = time.time()
    timestamps = _rate_store[session_id]

    # Prune entries older than the window
    _rate_store[session_id] = [t for t in timestamps if now - t < RATE_LIMIT_WINDOW]

    if len(_rate_store[session_id]) >= RATE_LIMIT_MAX:
        return True

    _rate_store[session_id].append(now)
    return False


# ---- Exception handler ----

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_exception path=%s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "intent": "error",
            "reply": "An unexpected internal error occurred.",
        },
    )


# ---- Admin Panel Route ----

@app.get("/admin.html")
async def serve_admin_panel():
    return FileResponse("admin.html")

# ---- Health check endpoint ----

@app.get("/")
def root():
    return {"message": "Frosty bot running "}

@app.get("/health")
def health():
    return {"status": "ok"} 


import secrets

# Store OAuth states temporarily in memory
# to prevent CSRF attacks
_oauth_states: dict = {}

@app.get("/auth/google")
async def google_auth_start(
    tenant_id: str = "default"
):
    """
    Start Google OAuth flow.
    Frontend calls this to get auth URL.
    """
    try:
        from services.google_auth import (
            get_authorization_url
        )
        url, state = get_authorization_url()
        
        # Store state with tenant_id
        _oauth_states[state] = tenant_id
        
        return JSONResponse({
            "success": True,
            "auth_url": url,
            "state": state
        })
    
    except Exception as e:
        logger.error(
            "google_auth_start failed: %s", e
        )
        return JSONResponse(
            {
                "success": False,
                "error": str(e)
            },
            status_code=500
        )


@app.get("/auth/google/callback")
async def google_auth_callback(
    code: str = None,
    state: str = None,
    error: str = None
):
    """
    Google OAuth callback.
    Exchanges code for tokens and saves them.
    """
    try:
        if error:
            logger.warning(
                "OAuth error: %s", error
            )
            return JSONResponse(
                {
                    "success": False,
                    "error": (
                        f"Google auth error: "
                        f"{error}"
                    )
                },
                status_code=400
            )
        
        if not code or not state:
            return JSONResponse(
                {
                    "success": False,
                    "error": "Missing code or state"
                },
                status_code=400
            )
        
        # Get tenant_id from state
        tenant_id = _oauth_states.pop(
            state, "default"
        )
        
        from services.google_auth import (
            exchange_code_for_tokens,
            get_user_email,
            save_tokens
        )
        
        # Exchange code for tokens
        tokens = exchange_code_for_tokens(
            code, state
        )
        
        # Get user email
        email = get_user_email(
            tokens["access_token"]
        )
        
        # Save tokens to DB
        saved = save_tokens(
            tenant_id, tokens, email
        )
        
        if saved:
            logger.info(
                "google_oauth_success "
                "tenant=%s email=%s",
                tenant_id, email
            )
            # Return HTML that closes popup
            # and notifies parent window
            return Response(
                content=f"""
                <html>
                <body>
                <script>
                window.opener.postMessage(
                    {{
                        type: 'GOOGLE_AUTH_SUCCESS',
                        email: '{email}'
                    }},
                    '*'
                );
                window.close();
                </script>
                <p>Connected successfully! 
                   You can close this window.</p>
                </body>
                </html>
                """,
                media_type="text/html"
            )
        else:
            return JSONResponse(
                {
                    "success": False,
                    "error": "Failed to save tokens"
                },
                status_code=500
            )
    
    except Exception as e:
        logger.error(
            "google_auth_callback failed: %s",
            e
        )
        return JSONResponse(
            {
                "success": False,
                "error": str(e)
            },
            status_code=500
        )


@app.get("/auth/google/status")
async def google_auth_status(
    tenant_id: str = "default"
):
    """Check if Google Calendar is connected."""
    try:
        from services.google_auth import (
            is_calendar_connected
        )
        from db_config import (
            get_connection, release_connection
        )
        
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT email, connected
                FROM google_oauth_tokens
                WHERE tenant_id = %s
            """, (tenant_id,))
            row = cur.fetchone()
            cur.close()
        finally:
            release_connection(conn)
        
        if row and row[1]:
            return JSONResponse({
                "connected": True,
                "email": row[0]
            })
        return JSONResponse({
            "connected": False,
            "email": None
        })
    
    except Exception as e:
        logger.error(
            "google_auth_status failed: %s", e
        )
        return JSONResponse({
            "connected": False,
            "email": None
        })


@app.delete("/auth/google/disconnect")
async def google_disconnect(
    tenant_id: str = "default"
):
    """Disconnect Google Calendar."""
    try:
        from db_config import (
            get_connection, release_connection
        )
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                UPDATE google_oauth_tokens
                SET connected = FALSE,
                    updated_at = 
                        CURRENT_TIMESTAMP
                WHERE tenant_id = %s
            """, (tenant_id,))
            conn.commit()
            cur.close()
        finally:
            release_connection(conn)
        
        return JSONResponse({
            "success": True,
            "message": "Calendar disconnected"
        })
    
    except Exception as e:
        logger.error(
            "google_disconnect failed: %s", e
        )
        return JSONResponse(
            {"success": False, "error": str(e)},
            status_code=500
        )


# ════ SLACK OAUTH ════

@app.get("/auth/slack")
async def slack_auth(
    tenant_id: str = "default"
):
    """
    Step 1: Generate Slack OAuth URL.
    Admin panel opens this in a popup.
    """
    from config import settings
    if not settings.SLACK_CLIENT_ID:
        raise HTTPException(
            status_code=500,
            detail="SLACK_CLIENT_ID not configured"
        )
    params = urllib.parse.urlencode({
        "client_id": settings.SLACK_CLIENT_ID,
        "scope": ",".join([
            "chat:write",
            "chat:write.public",
            "channels:read",
            "incoming-webhook",
        ]),
        "redirect_uri": (
            f"{settings.BASE_URL}"
            f"/auth/slack/callback"
        ),
        "state": tenant_id,
    })
    auth_url = (
        "https://slack.com/oauth/v2/authorize"
        f"?{params}"
    )
    return {
        "success": True,
        "auth_url": auth_url
    }


@app.get("/auth/slack/callback")
async def slack_callback(
    code: str = None,
    state: str = "default",
    error: str = None
):
    """
    Step 2: Slack redirects here after
    admin approves. Exchange code for
    bot token. Store in DB. Close popup.
    """
    from config import settings

    if error:
        return HTMLResponse(content=f"""
        <script>
          window.opener && window.opener
            .postMessage({{
              type: 'SLACK_AUTH_ERROR',
              error: '{error}'
            }}, '*');
          window.close();
        </script>
        """)

    if not code:
        return HTMLResponse(content="""
        <script>
          window.opener && window.opener
            .postMessage({{
              type: 'SLACK_AUTH_ERROR',
              error: 'no_code'
            }}, '*');
          window.close();
        </script>
        """)

    try:
        # Exchange code for token
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://slack.com/api/oauth.v2.access",
                data={
                    "client_id": settings.SLACK_CLIENT_ID,
                    "client_secret": settings.SLACK_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": (
                        f"{settings.BASE_URL}"
                        f"/auth/slack/callback"
                    ),
                }
            )
        data = resp.json()

        if not data.get("ok"):
            raise Exception(
                data.get("error", "oauth_failed")
            )

        bot_token    = data["access_token"]
        team_name    = data.get(
            "team", {}
        ).get("name", "")
        authed_user  = data.get(
            "authed_user", {}
        ).get("id", "")
        incoming     = data.get(
            "incoming_webhook", {}
        )
        channel_id   = incoming.get(
            "channel_id", ""
        )
        channel_name = incoming.get(
            "channel", ""
        )

        # Save to DB
        from session_store import (
            get_connection,
            release_connection
        )
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            """
            INSERT INTO slack_tokens
            (tenant_id, bot_token,
             channel_id, channel_name,
             team_name, authed_user,
             updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT (tenant_id)
            DO UPDATE SET
              bot_token    = EXCLUDED.bot_token,
              channel_id   = EXCLUDED.channel_id,
              channel_name = EXCLUDED.channel_name,
              team_name    = EXCLUDED.team_name,
              authed_user  = EXCLUDED.authed_user,
              updated_at   = NOW()
            """,
            (
                state, bot_token,
                channel_id, channel_name,
                team_name, authed_user
            )
        )
        conn.commit()
        cur.close()
        release_connection(conn)

        return HTMLResponse(content=f"""
        <script>
          window.opener && window.opener
            .postMessage({{
              type: 'SLACK_AUTH_SUCCESS',
              team: '{team_name}',
              channel: '{channel_name}'
            }}, '*');
          window.close();
        </script>
        """)

    except Exception as e:
        logger.error(
            "slack_callback error: %s", e
        )
        return HTMLResponse(content=f"""
        <script>
          window.opener && window.opener
            .postMessage({{
              type: 'SLACK_AUTH_ERROR',
              error: '{str(e)}'
            }}, '*');
          window.close();
        </script>
        """)


@app.get("/auth/slack/status")
async def slack_status(
    tenant_id: str = "default"
):
    """
    Check if Slack is connected
    for this tenant.
    """
    try:
        from session_store import (
            get_connection,
            release_connection
        )
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            """
            SELECT channel_name, team_name
            FROM slack_tokens
            WHERE tenant_id = %s
            """,
            (tenant_id,)
        )
        row = cur.fetchone()
        cur.close()
        release_connection(conn)

        if row:
            return {
                "connected": True,
                "channel": row[0],
                "team": row[1]
            }
        return {"connected": False}

    except Exception as e:
        logger.error(
            "slack_status error: %s", e
        )
        return {"connected": False}


@app.delete("/auth/slack/disconnect")
async def slack_disconnect(
    tenant_id: str = "default",
    x_admin_key: str = Header(None)
):
    """
    Disconnect Slack for this tenant.
    """
    if x_admin_key != settings.ADMIN_KEY:
        raise HTTPException(
            status_code=403,
            detail="Forbidden"
        )
    try:
        from session_store import (
            get_connection,
            release_connection
        )
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            "DELETE FROM slack_tokens "
            "WHERE tenant_id = %s",
            (tenant_id,)
        )
        conn.commit()
        cur.close()
        release_connection(conn)
        return {"success": True}
    except Exception as e:
        logger.error(
            "slack_disconnect error: %s", e
        )
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# ---- Chat endpoint ----

class ChatRequest(BaseModel):
    session_id: str
    message: Optional[str] = None
    audio: Optional[str] = None
    voice_response: Optional[bool] = False
    continuous_mode: Optional[bool] = False


YES_PHRASES = [
    "yes", "yeah", "yep", "yup", "sure", 
    "okay", "ok", "confirm", "confirmed",
    "send it", "go ahead", "do it", "please",
    "send", "yes please", "absolutely",
    "correct", "right", "affirmative"
]

NO_PHRASES = [
    "no", "nope", "nah", "cancel", "stop",
    "don't", "do not", "never mind", "nevermind",
    "abort", "skip", "negative", "not now"
]

def detect_yes_no(text: str) -> str:
    if not text:
        return "unclear"
    cleaned = text.lower().strip()
    cleaned = cleaned.replace(".", "").replace(
        ",", "").replace("!", "").replace("?", "")
    for phrase in YES_PHRASES:
        if phrase == cleaned or cleaned.startswith(phrase):
            return "yes"
    for phrase in NO_PHRASES:
        if phrase == cleaned or cleaned.startswith(phrase):
            return "no"
    return "unclear"


def is_echo_transcription(
    message: str,
    session_id: str
) -> bool:
    """
    Detect if transcribed audio is actually
    the bot's own previous response being
    picked up by the microphone (echo loop).
    Returns True if echo detected.
    """
    if not message or not session_id:
        return False
    
    message_lower = message.lower().strip()
    
    # Check 1 — Bot identity phrases
    # These phrases only appear in bot responses
    # never in genuine user messages
    bot_phrases = [
        "frosty, the ai assistant",
        "i'm frosty",
        "im frosty",
        "i am frosty",
        "as frosty",
        "frostrek llp's ai",
        "frostrek's ai assistant",
        "i'm here to help you with",
        "i'm here specifically to help",
        "how can i assist you today",
        "how can i help you today",
        "is there something about our",
        "best regards",
        "frosty | ai assistant",
        "the frostrek team",
        "powered by frosty",
    ]
    
    for phrase in bot_phrases:
        if phrase in message_lower:
            logger.warning(
                "echo_detected session=%s "
                "phrase='%s' message=%s",
                session_id,
                phrase,
                repr(message[:100])
            )
            return True
    
    # Check 2 — Compare against last 
    # assistant message in DB
    try:
        from session_store import get_session_history
        history = get_session_history(session_id)
        
        # Get last 3 assistant messages
        assistant_msgs = [
            h["content"] for h in history
            if h["role"] == "assistant"
        ][-3:]
        
        for prev_reply in assistant_msgs:
            if not prev_reply:
                continue
            
            prev_lower = prev_reply.lower().strip()
            
            # Exact match
            if message_lower == prev_lower:
                logger.warning(
                    "echo_detected exact_match "
                    "session=%s",
                    session_id
                )
                return True
            
            # High similarity — message is 
            # contained within bot reply
            # or bot reply starts with message
            if (
                len(message_lower) > 20 and
                message_lower in prev_lower
            ):
                logger.warning(
                    "echo_detected substring "
                    "session=%s",
                    session_id
                )
                return True
            
            # Check word overlap ratio
            msg_words = set(
                message_lower.split()
            )
            prev_words = set(
                prev_lower.split()
            )
            
            if len(msg_words) > 3:
                overlap = len(
                    msg_words & prev_words
                )
                ratio = overlap / len(msg_words)
                
                if ratio > 0.80:
                    logger.warning(
                        "echo_detected overlap=%.2f"
                        " session=%s",
                        ratio,
                        session_id
                    )
                    return True
    
    except Exception as e:
        logger.warning(
            "echo_check_failed: %s", e
        )
    
    return False


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
async def chat_endpoint(request: Request) -> Any:
    session_id = None
    try:
        content_type = request.headers.get("content-type", "")

        user_message = None
        user_id = None
        session_id = None
        message_type = None
        voice_response = False
        continuous_mode = False

        # -------------------------------
        # CASE 1: JSON TEXT REQUEST
        # -------------------------------
        if "application/json" in content_type:
            body = await request.json()

            user_id = body.get("user_id")
            session_id = body.get("session_id")
            user_message = body.get("message")
            message_type = body.get("type", "text")

            # Existing fields
            if not user_message and body.get("audio"):
                message_type = "voice"

            voice_response = body.get("voice_response", False)
            continuous_mode = body.get("continuous_mode", False)

            # Handle base64 audio fallback
            if message_type == "voice" and body.get("audio"):
                try:
                    audio_bytes = base64.b64decode(body.get("audio"))
                    user_message = transcribe_audio(audio_bytes)
                    if not user_message or user_message.strip() == "":
                        return JSONResponse(
                            sanitize_response(
                                "normal_chat",
                                "I didn't catch that. Could you please speak a little clearer or type your message?"
                            )
                        )
                    # Echo detection — reject if bot's 
                    # own voice was picked up by mic
                    if is_echo_transcription(
                        user_message,
                        str(session_id) if session_id else ""
                    ):
                        logger.info(
                            "echo_suppressed session=%s",
                            session_id
                        )
                        # Return silent response — empty reply
                        # so frontend plays no audio and 
                        # echo loop stops naturally
                        return JSONResponse({
                            "intent": "echo_suppressed",
                            "reply": "",
                            "continuous": False,
                            "silent": True
                        })
                except Exception as e:
                    logger.error("Audio decode error: %s", str(e))
                    return JSONResponse(
                        sanitize_response(
                            "normal_chat",
                            "I'm having trouble with the audio right now. Could you please type your message?"
                        ),
                        status_code=200
                    )

        # -------------------------------
        # CASE 2: MULTIPART (VOICE / IMAGE)
        # -------------------------------
        elif "multipart/form-data" in content_type:
            form = await request.form()

            user_id = form.get("user_id")
            session_id = form.get("session_id")
            message_type = form.get("type", "text")
        
            voice_response_val = form.get("voice_response", "")
            if isinstance(voice_response_val, str):
                voice_response = voice_response_val.lower() == "true"
            
            continuous_mode_val = form.get("continuous_mode", "")
            if isinstance(continuous_mode_val, str):
                continuous_mode = continuous_mode_val.lower() == "true"

            # Handle voice
            if message_type == "voice":
                from fastapi import UploadFile
                audio_file = form.get("audio")
            
                if not audio_file or not hasattr(audio_file, "read"):
                    return JSONResponse(sanitize_response("error", "Audio file missing", {"error": "Audio file missing"}), status_code=400)

                audio_bytes = await audio_file.read()

                try:
                    # Send to Whisper
                    user_message = transcribe_audio(audio_bytes)
                    if not user_message or user_message.strip() == "":
                        return JSONResponse(
                            sanitize_response(
                                "normal_chat",
                                "I didn't catch that. Could you please speak a little clearer or type your message?"
                            )
                        )
                    # Echo detection — reject if bot's 
                    # own voice was picked up by mic
                    if is_echo_transcription(
                        user_message,
                        str(session_id) if session_id else ""
                    ):
                        logger.info(
                            "echo_suppressed session=%s",
                            session_id
                        )
                        # Return silent response — empty reply
                        # so frontend plays no audio and 
                        # echo loop stops naturally
                        return JSONResponse({
                            "intent": "echo_suppressed",
                            "reply": "",
                            "continuous": False,
                            "silent": True
                        })
                except Exception as e:
                    logger.error("Whisper transcription failed: %s", e)
                    return JSONResponse(
                        sanitize_response(
                            "normal_chat",
                            "I'm having trouble with the audio right now. Could you please type your message?"
                        ),
                        status_code=200
                    )

            # Handle image (if needed later)
            elif message_type == "image":
                image_file = form.get("image")
                if image_file and hasattr(image_file, "read"):
                    image_bytes = await image_file.read()
                    user_message = "User sent an image."  # adapt if vision added

        else:
            return JSONResponse(sanitize_response("error", "Unsupported Content-Type", {"error": "Unsupported Content-Type"}), status_code=415)

        if not session_id or not str(session_id).strip():
            session_id = str(uuid.uuid4())

        if not user_message or not str(user_message).strip():
            return JSONResponse(
                sanitize_response(
                    "error", "Empty message",
                    {"error": "Empty message"}
                ), 
                status_code=400
            )

        # LLM-based message correction
        # Replaces all fuzzy/vocab correction
        try:
            import openai as _openai
            
            # Skip correction for very short
            # messages or those with emails
            _skip_correction = (
                len(user_message.strip()) <= 3
                or "@" in user_message
                or "http" in user_message.lower()
            )
            
            if not _skip_correction:
                _client = _openai.OpenAI(
                    api_key=settings.OPENAI_API_KEY
                )
                
                _result = _client.chat.completions\
                    .create(
                    model="gpt-4o-mini",
                    temperature=0,
                    max_tokens=200,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a transcription "
                                "corrector for a voice "
                                "assistant called Frosty "
                                "for Frostrek LLP, an AI "
                                "company in India.\n\n"
                                "Fix ONLY obvious "
                                "speech-to-text errors. "
                                "Common corrections:\n"
                                "- 'meat' → 'meet' ONLY when "
                                "clearly mispronounced (meat the "
                                "food). Never change the word "
                                "'meeting' to 'meet'.\n"
                                "- 'prostate/frostrak/"
                                "frostrack/frostack/"
                                "frosteck' → 'Frostrek'\n"
                                "- Repeated letters like "
                                "'schedulee/scheduleee' "
                                "→ 'schedule'\n"
                                "- 'at the rate' → '@'\n"
                                "- 'dot com' → '.com'\n"
                                "- 'dot' between words "
                                "in email context → '.'\n"
                                "- Common word "
                                "mishearings in business "
                                "context\n\n"
                                "STRICT RULES:\n"
                                "1. Return ONLY the "
                                "corrected sentence. "
                                "Nothing else.\n"
                                "2. Do NOT change meaning,"
                                " add words, or rewrite "
                                "the message.\n"
                                "3. If nothing needs "
                                "fixing, return the "
                                "original EXACTLY.\n"
                                "4. Never add quotes, "
                                "explanations, or "
                                "punctuation that wasn't "
                                "there.\n"
                                "5. Preserve all numbers,"
                                " names, and technical "
                                "terms exactly.\n"
                                "5. NEVER change 'meeting' → 'meet'."
                                " They are different words.\n"
                                "6. NEVER change correctly spelled "
                                "common words. Only fix clear "
                                "speech-to-text errors.\n"
                                "7. If unsure whether something is "
                                "an error, leave it exactly as is."
                            )
                        },
                        {
                            "role": "user",
                            "content": user_message
                        }
                    ]
                )
                
                _corrected = _result.choices[
                    0
                ].message.content.strip()
                
                # Safety check — reject if LLM
                # changed meaning too much
                # (word count difference > 4)
                _orig_words = len(
                    user_message.split()
                )
                _corr_words = len(
                    _corrected.split()
                )
                
                if abs(
                    _orig_words - _corr_words
                ) <= 4 and _corrected:
                    if _corrected.lower() != \
                            user_message.lower():
                        logger.info(
                            "llm_corrected "
                            "session=%s "
                            "'%s' → '%s'",
                            session_id,
                            repr(
                                user_message[:80]
                            ),
                            repr(_corrected[:80])
                        )
                        user_message = _corrected
                else:
                    logger.warning(
                        "llm_correction_rejected "
                        "session=%s "
                        "too_different: "
                        "'%s' → '%s'",
                        session_id,
                        repr(user_message[:60]),
                        repr(_corrected[:60])
                    )
        
        except Exception as e:
            logger.warning(
                "llm_correction_failed: %s", e
            )
            # Keep original message on failure

        # Rate limit check
        if _is_rate_limited(str(session_id)):
            logger.warning("Rate limit exceeded for session %s", session_id)
            return JSONResponse(sanitize_response("error", "Too many requests. Please slow down."))

        current_state = get_session_state(str(session_id))
        

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
                reply_text = str(
                    api_response.get("reply") or ""
                )
                if reply_text:
                    from tools.tts_tool import text_to_speech
                    
                    audio_bytes_tts = await text_to_speech(
                        reply_text
                    )
                
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


# ---- Admin endpoints ----

@app.post("/admin/upload")
async def admin_upload(
    file: UploadFile = File(...),
    category: str = Form("general")
):
    try:
        # Read uploaded file bytes
        content = await file.read()

        # Save to temp file with correct extension
        suffix = os.path.splitext(file.filename)[1]
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        # Use document_loader to extract and chunk text
        from services.document_loader import load_document
        chunks = load_document(tmp_path, file.filename)

        # Store chunks in the knowledge_base table
        from db_config import get_connection, release_connection
        conn = get_connection()
        try:
            cur = conn.cursor()
            safe_filename = file.filename.replace("\x00", "")
            for chunk in chunks:
                cur.execute(
                    """INSERT INTO knowledge_base 
                       (filename, category, 
                        chunk_text, created_at,
                        updated_at)
                       VALUES (
                           %s, %s, %s,
                           CURRENT_TIMESTAMP,
                           CURRENT_TIMESTAMP
                       )""",
                    (
                        safe_filename,
                        category,
                        sanitize_text(chunk["text"])
                    )
                )
            conn.commit()
            cur.close()
        finally:
            release_connection(conn)

        # Rebuild full RAG index including 
        # the newly uploaded document
        from services.rag_engine import refresh_index
        refresh_index()

        # Clean up temp file
        os.unlink(tmp_path)

        return JSONResponse({
            "success": True,
            "filename": file.filename,
            "category": category,
            "chunks": len(chunks),
            "message": f"Successfully indexed {file.filename}"
        })

    except Exception as e:
        logger.error(f"admin_upload_error: {e}", exc_info=True)
        return JSONResponse(
            {"success": False, "error": str(e)},
            status_code=500
        )


@app.get("/admin/documents")
async def admin_documents():
    try:
        from db_config import get_connection, release_connection
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT 
                    filename,
                    category,
                    COUNT(*) AS chunk_count,
                    MIN(created_at) AS upload_date
                FROM knowledge_base
                GROUP BY filename, category
                ORDER BY MIN(created_at) DESC
            """)
            rows = cur.fetchall()
            col_names = [
                desc[0] for desc in cur.description
            ]
            cur.close()
        finally:
            release_connection(conn)

        docs = []
        for r in rows:
            row_dict = dict(zip(col_names, r))
            docs.append({
                "filename": row_dict.get(
                    "filename", ""
                ),
                "category": row_dict.get(
                    "category", ""
                ),
                "chunk_count": int(
                    row_dict.get("chunk_count", 0)
                ),
                "upload_date": (
                    row_dict["upload_date"]
                    .strftime("%Y-%m-%d %H:%M")
                    if row_dict.get("upload_date")
                    else "Unknown"
                )
            })

        return JSONResponse({
            "success": True,
            "documents": docs
        })

    except Exception as e:
        logger.error(f"admin_documents_error: {e}")
        return JSONResponse({
            "success": True,
            "documents": []
        })


@app.get("/admin/leads")
async def admin_leads():
    try:
        from db_config import get_connection, release_connection
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT name, email,
                       session_id, created_at
                FROM leads
                ORDER BY created_at DESC
            """)
            rows = cur.fetchall()
            cur.close()
        finally:
            release_connection(conn)

        leads = [
            {
                "name": r[0],
                "email": r[1],
                "session_id": r[2],
                "created_at": str(r[3])
            }
            for r in rows
        ]

        return JSONResponse({
            "success": True,
            "leads": leads
        })

    except Exception as e:
        logger.error(f"admin_leads_error: {e}")
        return JSONResponse({
            "success": True,
            "leads": []
        })


@app.delete("/admin/documents/{filename:path}")
async def admin_delete_document(
    filename: str
):
    try:
        # URL decode the filename
        from urllib.parse import unquote
        filename = unquote(filename)
        
        if not filename or not filename.strip():
            return JSONResponse(
                {
                    "success": False,
                    "error": "Filename is required"
                },
                status_code=400
            )
        
        from db_config import (
            get_connection, release_connection
        )
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                DELETE FROM knowledge_base 
                WHERE filename = %s
                """,
                (filename.strip(),)
            )
            deleted = cur.rowcount
            conn.commit()
            cur.close()
        finally:
            release_connection(conn)
        
        if deleted == 0:
            return JSONResponse(
                {
                    "success": False,
                    "error": (
                        f"No document found "
                        f"with filename: {filename}"
                    )
                },
                status_code=404
            )
        
        # Rebuild RAG index
        from services.rag_engine import (
            refresh_index
        )
        refresh_index()
        
        logger.info(
            "admin_delete_success "
            "filename=%s chunks_deleted=%d",
            filename, deleted
        )
        
        return JSONResponse({
            "success": True,
            "filename": filename,
            "chunks_deleted": deleted,
            "message": (
                f"Deleted {filename} and "
                f"rebuilt search index."
            )
        })
    
    except Exception as e:
        logger.error(
            f"admin_delete_error: {e}",
            exc_info=True
        )
        return JSONResponse(
            {
                "success": False,
                "error": str(e)
            },
            status_code=500
        )


@app.delete("/admin/delete-doc")
async def admin_delete_doc(
    filename: str = None,
    request: Request = None
):
    try:
        # Get filename from query parameter
        # Frontend sends: DELETE /admin/delete-doc
        #                 ?filename=text_info.txt
        if not filename:
            # Try to get from request params
            params = dict(request.query_params)
            filename = params.get("filename", "")
        
        from urllib.parse import unquote
        filename = unquote(
            filename or ""
        ).strip()
        
        if not filename:
            return JSONResponse(
                {
                    "success": False,
                    "error": "Filename is required"
                },
                status_code=400
            )
        
        from db_config import (
            get_connection, release_connection
        )
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                DELETE FROM knowledge_base 
                WHERE filename = %s
                """,
                (filename,)
            )
            deleted = cur.rowcount
            conn.commit()
            cur.close()
        finally:
            release_connection(conn)
        
        if deleted == 0:
            return JSONResponse(
                {
                    "success": False,
                    "error": (
                        f"No document found "
                        f"with filename: {filename}"
                    )
                },
                status_code=404
            )
        
        # Rebuild RAG index
        from services.rag_engine import (
            refresh_index
        )
        refresh_index()
        
        logger.info(
            "admin_delete_success "
            "filename=%s chunks=%d",
            filename, deleted
        )
        
        return JSONResponse({
            "success": True,
            "filename": filename,
            "chunks_deleted": deleted,
            "message": (
                f"Deleted {filename} and "
                f"rebuilt search index."
            )
        })
    
    except Exception as e:
        logger.error(
            f"admin_delete_doc_error: {e}",
            exc_info=True
        )
        return JSONResponse(
            {
                "success": False,
                "error": str(e)
            },
            status_code=500
        )


@app.get("/admin/sessions")
async def admin_get_all_sessions(
    request: Request
):
    x_admin_key = request.headers.get("x-admin-key", "")
    if x_admin_key != settings.ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    from session_store import (
        get_all_sessions_summary
    )
    return {
        "sessions": get_all_sessions_summary()
    }


@app.get("/admin/sessions/{session_id}")
async def admin_get_session_history(
    session_id: str,
    x_admin_key: str = Header(None)
):
    if x_admin_key != settings.ADMIN_KEY:
        raise HTTPException(
            status_code=403,
            detail="Forbidden"
        )
    from session_store import get_full_history
    messages = get_full_history(session_id)
    return {
        "session_id": session_id,
        "messages": messages,
        "total": len(messages)
    }


@app.get("/admin/users/{email}/sessions")
async def admin_get_user_sessions(
    email: str,
    request: Request
):
    x_admin_key = request.headers.get("x-admin-key", "")
    if x_admin_key != settings.ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    from session_store import (
        get_sessions_by_email,
        get_full_history
    )
    sessions = get_sessions_by_email(email)
    for s in sessions:
        s["messages"] = get_full_history(
            s["session_id"]
        )
    return {
        "email": email,
        "total_sessions": len(sessions),
        "sessions": sessions
    }


@app.get("/admin/bot-config")
async def get_bot_config_endpoint(request: Request):
    x_admin_key = request.headers.get("x-admin-key", "")
    if x_admin_key != settings.ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    from services.bot_config import get_bot_config
    return get_bot_config("default")

@app.post("/admin/bot-config")
async def save_bot_config_endpoint(request: Request):
    x_admin_key = request.headers.get("x-admin-key", "")
    if x_admin_key != settings.ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    body = await request.json()
    allowed = [
        "bot_name", "company_name", "company_short",
        "tone", "business_type",
        "contact_email", "working_hours"
    ]
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(
            status_code=400, detail="No valid fields"
        )
    from services.bot_config import update_bot_config
    success = update_bot_config("default", updates)
    if success:
        return {"success": True}
    raise HTTPException(
        status_code=500, detail="Failed to save"
    )

@app.get("/admin/bot-config/model")
async def get_active_model(request: Request):
    x_admin_key = request.headers.get("x-admin-key", "")
    if x_admin_key != settings.ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    from services.bot_config import get_bot_config
    config = get_bot_config("default")
    return {"active_model": config.get("active_model", "openai")}

@app.post("/admin/bot-config/model")
async def save_active_model(request: Request):
    x_admin_key = request.headers.get("x-admin-key", "")
    if x_admin_key != settings.ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    body = await request.json()
    model = body.get("model", "")
    if model not in ["openai", "gemini"]:
        raise HTTPException(
            status_code=400,
            detail="Invalid model. Choose openai or gemini."
        )
    from services.bot_config import update_active_model
    success = update_active_model("default", model)
    if success:
        return {"success": True, "active_model": model}
    raise HTTPException(
        status_code=500,
        detail="Failed to update model"
    )

@app.get("/admin/analytics/overview")
async def analytics_overview(request: Request, days: int = 7):
    x_admin_key = request.headers.get("x-admin-key", "")
    if x_admin_key != settings.ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    conn = None
    try:
        from db_config import get_connection, release_connection
        conn = get_connection()
        cur = conn.cursor()

        # Total conversations
        cur.execute("""
            SELECT COUNT(DISTINCT session_id)
            FROM conversation_memory
            WHERE created_at >= NOW() - INTERVAL '%s days'
        """, (days,))
        total_conversations = cur.fetchone()[0]

        # Total messages
        cur.execute("""
            SELECT COUNT(*)
            FROM conversation_memory
            WHERE created_at >= NOW() - INTERVAL '%s days'
        """, (days,))
        total_messages = cur.fetchone()[0]

        # Total leads
        cur.execute("""
            SELECT COUNT(*)
            FROM leads
            WHERE created_at >= NOW() - INTERVAL '%s days'
        """, (days,))
        total_leads = cur.fetchone()[0]

        # Avg messages per session
        cur.execute("""
            SELECT ROUND(AVG(msg_count)::numeric, 1)
            FROM (
                SELECT session_id, COUNT(*) as msg_count
                FROM conversation_memory
                WHERE created_at >= NOW() - INTERVAL '%s days'
                GROUP BY session_id
            ) s
        """, (days,))
        avg_messages_per_session = cur.fetchone()[0] or 0

        cur.close()
        return {
            "total_conversations": total_conversations,
            "total_messages": total_messages,
            "total_leads": total_leads,
            "avg_messages_per_session": float(avg_messages_per_session),
            "days": days
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            from db_config import release_connection
            release_connection(conn)

@app.get("/admin/analytics/conversations-over-time")
async def analytics_conversations_over_time(request: Request, days: int = 7):
    x_admin_key = request.headers.get("x-admin-key", "")
    if x_admin_key != settings.ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    conn = None
    try:
        from db_config import get_connection, release_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT
                DATE(created_at) as day,
                COUNT(DISTINCT session_id) as conversations,
                COUNT(*) as messages
            FROM conversation_memory
            WHERE created_at >= NOW() - INTERVAL '%s days'
            GROUP BY DATE(created_at)
            ORDER BY day ASC
        """, (days,))
        rows = cur.fetchall()
        cur.close()
        return {
            "data": [
                {
                    "date": str(row[0]),
                    "conversations": row[1],
                    "messages": row[2]
                }
                for row in rows
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            from db_config import release_connection
            release_connection(conn)

@app.get("/admin/analytics/leads-over-time")
async def analytics_leads_over_time(request: Request, days: int = 7):
    x_admin_key = request.headers.get("x-admin-key", "")
    if x_admin_key != settings.ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    conn = None
    try:
        from db_config import get_connection, release_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT
                DATE(created_at) as day,
                COUNT(*) as leads
            FROM leads
            WHERE created_at >= NOW() - INTERVAL '%s days'
            GROUP BY DATE(created_at)
            ORDER BY day ASC
        """, (days,))
        rows = cur.fetchall()
        cur.close()
        return {
            "data": [
                {"date": str(row[0]), "leads": row[1]}
                for row in rows
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            from db_config import release_connection
            release_connection(conn)

@app.get("/admin/analytics/top-topics")
async def analytics_top_topics(request: Request, days: int = 7):
    x_admin_key = request.headers.get("x-admin-key", "")
    if x_admin_key != settings.ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    conn = None
    try:
        from db_config import get_connection, release_connection
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT content
            FROM conversation_memory
            WHERE role = 'human'
            AND created_at >= NOW() - INTERVAL '%s days'
            ORDER BY created_at DESC
            LIMIT 200
        """, (days,))
        rows = cur.fetchall()
        cur.close()

        from collections import Counter
        import re
        stop_words = {
            'i', 'me', 'my', 'we', 'our', 'you', 'your', 'the',
            'a', 'an', 'is', 'are', 'was', 'be', 'to', 'of', 'and',
            'in', 'it', 'for', 'on', 'with', 'as', 'at', 'by',
            'this', 'that', 'do', 'can', 'will', 'how', 'what',
            'hi', 'hello', 'hey', 'please', 'tell', 'me', 'give',
            'about', 'have', 'has', 'had', 'not', 'but', 'from',
            'or', 'so', 'if', 'up', 'out', 'no', 'its', 'than',
            'then', 'them', 'they', 'their', 'there', 'when',
            'which', 'who', 'get', 'got', 'just', 'more', 'also'
        }
        words = []
        for row in rows:
            tokens = re.findall(r'\b[a-z]{4,}\b', row[0].lower())
            words.extend([w for w in tokens if w not in stop_words])
        top = Counter(words).most_common(10)
        return {
            "topics": [{"word": w, "count": c} for w, c in top]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            from db_config import release_connection
            release_connection(conn)


@app.get("/cancel-meeting")
async def cancel_meeting_via_link(
    event_id: str,
    email: str,
    name: str = "there",
    title: str = "Meeting"
):
    from tools.calendar_tool import cancel_meeting
    from services.email_service import send_cancellation_email
    from services.bot_config import get_bot_config
    from config import settings

    config = get_bot_config("default")
    admin_email = config.get(
        "contact_email", settings.SMTP_FROM
    )

    result = cancel_meeting(event_id)

    if not result.get("success"):
        return HTMLResponse(content="""
        <html><body style='font-family:sans-serif;
        text-align:center;padding:50px;
        background:#0a0a0a;color:white'>
        <h2>Could not cancel meeting</h2>
        <p>Please contact us at """ + admin_email + """</p>
        </body></html>
        """)

    send_cancellation_email(
        to_email=email,
        participant_name=name,
        meeting_title=title,
        meeting_time="as scheduled",
        admin_email=admin_email
    )

    return HTMLResponse(content="""
    <html><body style='font-family:sans-serif;
    text-align:center;padding:50px;
    background:#0a0a0a;color:white'>
    <h1 style='color:#00d4ff'>Meeting Cancelled</h1>
    <p>Your meeting has been successfully cancelled.</p>
    <p>A confirmation email has been sent to """ + email + """</p>
    <p style='color:#888;margin-top:40px'>
    Want to reschedule? Start a new chat with us.</p>
    </body></html>
    """)
