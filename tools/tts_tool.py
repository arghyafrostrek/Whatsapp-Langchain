from openai import OpenAI
from config import settings
from logger_config import logger

openai_client = OpenAI(
    api_key=settings.OPENAI_API_KEY
)

async def text_to_speech(text: str) -> bytes:
    try:
        response = openai_client.audio.speech.create(
            model="tts-1",
            voice="alloy",
            input=text
        )
        logger.info(
            "tts_success length=%d", len(text)
        )
        return response.content
    except Exception as e:
        logger.error("tts_failed: %s", e)
        raise
