import requests
import os
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "http://localhost:8000"
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "frosty2026")

def test_whatsapp_verify():
    print(f"\n--- Testing WhatsApp Webhook Verification ---")
    params = {
        "hub.mode": "subscribe",
        "hub.verify_token": VERIFY_TOKEN,
        "hub.challenge": "challenge_123"
    }
    try:
        response = requests.get(f"{BASE_URL}/webhook/whatsapp", params=params)
        if response.status_code == 200 and response.text == "challenge_123":
            print("SUCCESS: Webhook verified successfully.")
            return True
        else:
            print(f"FAILED (Status: {response.status_code}, Body: {response.text})")
            return False
    except Exception as e:
        print(f"ERROR: {e}")
        return False

def test_whatsapp_message():
    print(f"\n--- Testing WhatsApp Message Webhook ---")
    # Mock WhatsApp payload
    payload = {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "123456789",
            "changes": [{
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {
                        "display_phone_number": "1234567890",
                        "phone_number_id": "1087073191148693"
                    },
                    "contacts": [{
                        "profile": {"name": "Test User"},
                        "wa_id": "1234567890"
                    }],
                    "messages": [{
                        "from": "1234567890",
                        "id": "wamid.HBgLMTIzNDU2Nzg5MBVfAhgUM0FCRDBDQkU1",
                        "timestamp": "1660000000",
                        "text": {"body": "Hello from WhatsApp!"},
                        "type": "text"
                    }]
                },
                "field": "messages"
            }]
        }]
    }
    try:
        response = requests.post(f"{BASE_URL}/webhook/whatsapp", json=payload)
        if response.status_code == 200:
            print("SUCCESS: Message webhook accepted.")
            print("Note: Processing happens in the background. Check server logs for details.")
            return True
        else:
            print(f"FAILED (Status: {response.status_code}, Body: {response.text})")
            return False
    except Exception as e:
        print(f"ERROR: {e}")
        return False

if __name__ == "__main__":
    test_whatsapp_verify()
    test_whatsapp_message()
