import tempfile

from openai import OpenAI
from config import settings
from logger_config import logger


def transcribe_audio(audio_path_or_bytes: str | bytes) -> str:
    """
    Transcribe audio via OpenAI Whisper.
    Accepts either raw bytes or a file path to an .ogg / .webm file.
    Returns transcribed text.
    Raises RuntimeError on failure.
    """
    # Handle if it's a file path
    is_path = isinstance(audio_path_or_bytes, str)
    
    if not is_path:
        if not audio_path_or_bytes or len(audio_path_or_bytes) < 3000:
            logger.warning(
                "audio_rejected_too_short bytes=%d",
                len(audio_path_or_bytes) if audio_path_or_bytes else 0
            )
            raise RuntimeError(
                "Whisper processing failed: "
                "Audio too short to transcribe"
            )

    try:
        client = OpenAI(api_key=settings.OPENAI_API_KEY)

        if is_path:
            with open(audio_path_or_bytes, "rb") as f:
                transcription = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    language="en"
                )
        else:
            transcription = client.audio.transcriptions.create(
                model="whisper-1",
                file=(
                    "voice-message.ogg",
                    audio_path_or_bytes,
                    "audio/ogg"
                ),
                language="en"
            )

        text = transcription.text or ""
        if not text.strip():
            raise ValueError("Whisper returned empty transcription")
        
        logger.info("whisper_transcription_success length=%s", len(text))
    
        return text.strip()

    except Exception as e:
        logger.error("whisper_transcription_failed: %s", e)
        raise RuntimeError(f"Whisper processing failed: {str(e)}")
