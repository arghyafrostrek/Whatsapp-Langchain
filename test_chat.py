import requests
import json
import time

BASE_URL = "http://localhost:8000"

def test_health():
    print(f"Testing {BASE_URL}/health ...", end=" ")
    try:
        response = requests.get(f"{BASE_URL}/health", timeout=5)
        if response.status_code == 200:
            print(f"SUCCESS: {response.json()}")
            return True
        else:
            print(f"FAILED (Status: {response.status_code}, Body: {response.text})")
            return False
    except Exception as e:
        print(f"ERROR: {e}")
        return False

def test_chat(message):
    print(f"\nTesting /chat with message: '{message}'")
    payload = {
        "session_id": "test_session_" + str(int(time.time())),
        "message": message
    }
    try:
        response = requests.post(f"{BASE_URL}/chat", json=payload, timeout=30)
        if response.status_code == 200:
            data = response.json()
            print(f"  [+] Intent: {data.get('intent')}")
            print(f"  [+] Reply: {data.get('reply')}")
            return True
        else:
            print(f"  [-] FAILED (Status: {response.status_code}, Body: {response.text})")
            return False
    except Exception as e:
        print(f"  [-] ERROR: {e}")
        return False

if __name__ == "__main__":
    print("--- Starting Frosty API Tests ---")
    if test_health():
        test_chat("Hello Frosty!")
        test_chat("Schedule a meeting titled 'Design Sync' tomorrow at 3pm with bob@example.com")
    print("\n--- Tests Completed ---")
