import sys
from db_config import get_connection

def clear_sessions():
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM session_state")
        conn.commit()
        print("Successfully cleared all session states.")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if 'cur' in locals() and cur:
            cur.close()
        if 'conn' in locals() and conn:
            conn.close()

if __name__ == "__main__":
    clear_sessions()
