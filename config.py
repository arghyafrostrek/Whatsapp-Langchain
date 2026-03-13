import os
from dotenv import load_dotenv

# Load env variables explicitly
load_dotenv()


class Settings:
    def __init__(self) -> None:
        self.OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
        self.ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
        self.SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
        self.GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
        self.DATABASE_URL = os.getenv("DATABASE_URL")
        self.GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
        self.GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
        self.SLACK_CLIENT_ID = os.getenv(
            "SLACK_CLIENT_ID", ""
        )
        self.SLACK_CLIENT_SECRET = os.getenv(
            "SLACK_CLIENT_SECRET", ""
        )
        self.WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
        self.WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "")
        self.WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
        self.BASE_URL = os.getenv(
            "BASE_URL",
            "http://localhost:8000"
        )
        self.ADMIN_KEY = os.getenv("ADMIN_KEY", "admin123")

        if not self.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY is missing in environment configuration.")


settings = Settings()
