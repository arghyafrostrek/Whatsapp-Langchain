"""
Input Router — normalizes WhatsApp webhook payloads.

Detects message type (text, voice, image) and converts
to a common dict understood by the LangGraph agent.
"""

import os
from typing import Any, Optional

from logger_config import logger


def route_input(message: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize a single WhatsApp message dict into:
        {
            "input_type": "text" | "voice" | "image",
            "text": str,            # final text for the agent
            "media_path": str|None,  # local path to downloaded file
            "original": dict,        # raw message for audit
        }
    """
    msg_type = message.get("type", "text")

    if msg_type == "text":
        return _handle_text(message)
    elif msg_type in ("audio", "voice"):
        return _handle_voice(message)
    elif msg_type == "image":
        return _handle_image(message)
    else:
        # Fallback: treat unknown types as text
        body = (
            message.get("text", {}).get("body")
            or message.get("body")
            or ""
        )
        logger.warning(
            "input_router: unknown type '%s', falling back to text",
            msg_type,
        )
        return {
            "input_type": "text",
            "text": body,
            "media_path": None,
            "original": message,
        }


# ── Handlers ─────────────────────────────────

def _handle_text(message: dict) -> dict:
    body = message.get("text", {}).get("body", "")
    return {
        "input_type": "text",
        "text": body.strip(),
        "media_path": None,
        "original": message,
    }


def _handle_voice(message: dict) -> dict:
    """Download voice note and transcribe via existing voice tool."""
    audio_info = message.get("audio") or message.get("voice") or {}
    media_id = audio_info.get("id")

    if not media_id:
        logger.error("input_router: voice message with no media id")
        return {
            "input_type": "voice",
            "text": "",
            "media_path": None,
            "original": message,
        }

    # Download the audio file
    from services.whatsapp import download_media
    audio_path = download_media(media_id)

    if not audio_path:
        return {
            "input_type": "voice",
            "text": "[Voice message could not be processed]",
            "media_path": None,
            "original": message,
        }

    # Transcribe using existing voice tool
    try:
        from tools.voice_tool import transcribe_audio
        transcript = transcribe_audio(audio_path)
        text = transcript if isinstance(transcript, str) else str(transcript)
    except Exception as exc:
        logger.error("input_router: transcription failed: %s", exc)
        text = "[Voice message could not be transcribed]"

    # Clean up temp file
    _safe_delete(audio_path)

    return {
        "input_type": "voice",
        "text": text.strip(),
        "media_path": audio_path,
        "original": message,
    }


def _handle_image(message: dict) -> dict:
    """Download image and use caption (or describe via LLM)."""
    image_info = message.get("image", {})
    media_id = image_info.get("id")
    caption = image_info.get("caption", "")

    image_path: Optional[str] = None

    if media_id:
        from services.whatsapp import download_media
        image_path = download_media(media_id)

    # If user sent a caption, use it as the text
    if caption:
        text = caption.strip()
    elif image_path:
        text = _describe_image(image_path)
    else:
        text = "[Image received]"

    return {
        "input_type": "image",
        "text": text,
        "media_path": image_path,
        "original": message,
    }


def _describe_image(image_path: str) -> str:
    """Use Gemini or OpenAI vision to describe an image."""
    try:
        import base64
        from langchain_openai import ChatOpenAI

        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()

        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, max_tokens=200)
        from langchain_core.messages import HumanMessage

        msg = HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": (
                        "Describe this image in one sentence. "
                        "Focus on what the user might be asking about."
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{img_b64}"
                    },
                },
            ]
        )
        resp = llm.invoke([msg])
        return f"[User sent an image: {resp.content}]"

    except Exception as exc:
        logger.error("input_router: image description failed: %s", exc)
        return "[User sent an image]"


def _safe_delete(path: str) -> None:
    """Delete a file without raising."""
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except Exception:
        pass
