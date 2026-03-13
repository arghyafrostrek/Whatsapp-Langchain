"""
Chat logging module for analytics-grade conversation logs.

Writes every user/assistant exchange to a chat_logs table with
session_id, role, message, intent, and timestamp.
"""

from logger_config import logger
from db_config import get_connection, release_connection


def log_chat(
    session_id: str,
    role: str,
    message: str,
    intent: str = "",
    user_id: str = "",
) -> None:
    """
    Log a chat message to the chat_logs table.
    
    Args:
        session_id: The session identifier.
        role: "user" or "assistant".
        message: The message content.
        intent: The detected intent (optional).
        user_id: The user identifier (optional).
    """
    if not session_id or not message:
        return

    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO chat_logs (session_id, user_id, role, message, intent)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (session_id, user_id or "", role, message, intent or ""),
        )
        conn.commit()
    except Exception as e:
        logger.error("log_chat failed for session %s: %s", session_id, e)
    finally:
        if cur:
            try: cur.close()
            except Exception: pass
        if conn:
            release_connection(conn)
