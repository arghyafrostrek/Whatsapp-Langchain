"""
WhatsApp Cloud API service.

Provides helpers to:
  1. Send text messages
  2. Download media (voice, image)
  3. Mark messages as read
"""

import os
import tempfile
from typing import Optional

import httpx

from logger_config import logger

GRAPH_API_VERSION = "v21.0"
BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


def _get_credentials() -> tuple[str, str]:
    """Return (phone_number_id, access_token)."""
    phone_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    token = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
    if not phone_id or not token:
        raise RuntimeError(
            "Missing WHATSAPP_PHONE_NUMBER_ID or "
            "WHATSAPP_ACCESS_TOKEN in environment."
        )
    return phone_id, token


def _headers() -> dict[str, str]:
    _, token = _get_credentials()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


# ── Send text reply ─────────────────────────

def send_text_message(
    to_phone: str,
    text: str,
    phone_id: Optional[str] = None,
) -> bool:
    """
    Send a plain-text WhatsApp message.

    Args:
        to_phone: recipient phone number (E.164 without '+')
        text: message body
        phone_id: specific WhatsApp Business Phone Number ID

    Returns:
        True on success, False on failure.
    """
    env_phone_id, token = _get_credentials()
    actual_phone_id = phone_id or env_phone_id
    url = f"{BASE_URL}/{actual_phone_id}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_phone,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }

    try:
        resp = httpx.post(url, json=payload, headers=_headers(), timeout=15)
        if resp.status_code == 200:
            logger.info(
                "whatsapp: message sent to %s…%s",
                to_phone[:3], to_phone[-4:],
            )
            return True
        logger.error(
            "whatsapp: send failed status=%s body=%s",
            resp.status_code, resp.text[:300],
        )
        return False
    except Exception as exc:
        logger.error("whatsapp: send exception: %s", exc)
        return False


# ── Send audio reply ─────────────────────────

def send_audio_message(
    to_phone: str,
    audio_path: str,
    phone_id: Optional[str] = None,
) -> bool:
    """
    Upload an audio file to Meta Media API and send it as a voice note.
    """
    env_phone_id, token = _get_credentials()
    actual_phone_id = phone_id or env_phone_id
    
    # 1. Upload Media
    upload_url = f"{BASE_URL}/{actual_phone_id}/media"
    
    import mimetypes
    mime_type, _ = mimetypes.guess_type(audio_path)
    if not mime_type:
        mime_type = "audio/mpeg"
        
    try:
        with open(audio_path, "rb") as f:
            files = {
                "file": (os.path.basename(audio_path), f, mime_type)
            }
            data = {
                "messaging_product": "whatsapp"
            }
            upload_headers = {"Authorization": f"Bearer {token}"}
            # httpx handles multipart/form-data boundary when files= is passed
            resp = httpx.post(upload_url, headers=upload_headers, data=data, files=files, timeout=30)
            
        resp.raise_for_status()
        media_id = resp.json().get("id")
        if not media_id:
            logger.error("whatsapp: failed to get media_id on upload")
            return False
            
        # 2. Send Media Message
        msg_url = f"{BASE_URL}/{actual_phone_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to_phone,
            "type": "audio",
            "audio": {"id": media_id}
        }
        resp2 = httpx.post(msg_url, json=payload, headers=_headers(), timeout=15)
        resp2.raise_for_status()
        logger.info("whatsapp: audio sent to %s", to_phone)
        return True
    except Exception as exc:
        logger.error("whatsapp: send audio exception: %s", exc)
        return False


# ── Download media ───────────────────────────

def download_media(media_id: str) -> Optional[str]:
    """
    Download a media file from WhatsApp servers.

    1. GET the media URL from the Graph API.
    2. Stream-download the file to a temp path.

    Returns:
        Absolute path to the downloaded file, or None.
    """
    _, token = _get_credentials()
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "curl/7.68.0"
    }

    try:
        # Step 1 – get the download URL
        meta_url = f"{BASE_URL}/{media_id}"
        meta_resp = httpx.get(meta_url, headers=headers, timeout=15)
        meta_resp.raise_for_status()
        media_url = meta_resp.json().get("url")
        mime_type = meta_resp.json().get("mime_type", "")

        if not media_url:
            logger.error(f"whatsapp: media url not found for id={media_id}")
            return None

        # Step 2 – download the actual content
        # Some Meta URLs (lookaside.fbsbx.com) are signed and might reject the 
        # Authorization header if it is redundant with the URL hash.
        # We try with header first, then without if 401 occurs.
        
        ext = _mime_to_ext(mime_type)
        tmp_file = tempfile.NamedTemporaryFile(
            delete=False, suffix=ext, prefix="wa_media_"
        )
        save_path = tmp_file.name
        tmp_file.close() # Close the file handle, we'll open it again for writing

        for attempt in ["with_auth", "no_auth"]:
            current_headers = headers if attempt == "with_auth" else {"User-Agent": headers["User-Agent"]}
            
            with httpx.stream(
                "GET", media_url, headers=current_headers, timeout=30, follow_redirects=True
            ) as stream:
                if stream.status_code == 200:
                    with open(save_path, "wb") as f:
                        for chunk in stream.iter_bytes():
                            f.write(chunk)
                    logger.info(
                        "whatsapp: downloaded media %s → %s (%s)",
                        media_id, save_path, mime_type,
                    )
                    return save_path
                
                if stream.status_code == 401 and attempt == "with_auth":
                    logger.warning(f"whatsapp: 401 with auth for {media_id}, retrying without auth...")
                    continue
                    
                logger.error(f"whatsapp: media download failed id={media_id} attempt={attempt}: {stream.status_code} {stream.text[:100]}")
                break

        # If loop finishes without returning, it means download failed after retries
        os.remove(save_path) # Clean up the empty temp file
        return None

    except Exception as exc:
        logger.error(
            "whatsapp: media download failed id=%s: %s",
            media_id, exc,
        )
        return None


def _mime_to_ext(mime: str) -> str:
    """Map common MIME types to file extensions."""
    mapping = {
        "audio/ogg": ".ogg",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "video/mp4": ".mp4",
    }
    return mapping.get(mime.lower().split(";")[0], ".bin")


# ── Mark as read ─────────────────────────────

def mark_as_read(message_id: str, phone_id: Optional[str] = None) -> None:
    """Mark an incoming WhatsApp message as read."""
    try:
        env_phone_id, token = _get_credentials()
        actual_phone_id = phone_id or env_phone_id
        url = f"{BASE_URL}/{actual_phone_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        httpx.post(url, json=payload, headers=_headers(), timeout=10)
    except Exception as exc:
        logger.warning("whatsapp: mark_as_read failed: %s", exc)
