"""
Conversation summarization module.

Generates intelligent summaries of long conversations using OpenAI,
stores them in a dedicated conversation_summary table, and prunes
old history to keep the active window small.
"""

from typing import List, Dict

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from config import settings
from logger_config import logger
from db_config import get_connection, release_connection


# ---- Database operations ----

def get_conversation_summary(session_id: str) -> str:
    """Retrieve the stored conversation summary for a session."""
    if not session_id:
        return ""

    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT summary FROM conversation_summary WHERE session_id = %s",
            (session_id,),
        )
        row = cur.fetchone()
        return row[0] if row and row[0] else ""
    except Exception as e:
        logger.error("get_conversation_summary failed for session %s: %s", session_id, e)
        return ""
    finally:
        if cur:
            try: cur.close()
            except Exception: pass
        if conn:
            release_connection(conn)


def save_conversation_summary(session_id: str, summary: str) -> None:
    """Insert or update the conversation summary for a session."""
    if not session_id or not summary:
        return

    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO conversation_summary (session_id, summary)
            VALUES (%s, %s)
            ON CONFLICT (session_id)
            DO UPDATE SET summary = EXCLUDED.summary, updated_at = CURRENT_TIMESTAMP
            """,
            (session_id, summary),
        )
        conn.commit()
        logger.info("conversation_summary_saved session=%s length=%s", session_id, len(summary))
    except Exception as e:
        logger.error("save_conversation_summary failed for session %s: %s", session_id, e)
    finally:
        if cur:
            try: cur.close()
            except Exception: pass
        if conn:
            release_connection(conn)


# ---- Summarization logic ----

def summarize_conversation(history: List[Dict[str, str]], previous_summary: str = "") -> str:
    """
    Use OpenAI gpt-4o-mini to summarize a conversation.
    Preserves important facts, user preferences, and pending tasks.
    """
    if not history:
        return previous_summary or ""

    try:
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            api_key=settings.OPENAI_API_KEY,
            max_tokens=500,
            temperature=0.3,
        )

        messages = [
            SystemMessage(content=(
                "Summarize this conversation briefly but preserve important facts, "
                "user preferences, pending tasks, and any commitments made."
            ))
        ]

        if previous_summary:
            messages.append(SystemMessage(content=f"Previous summary: {previous_summary}"))

        for msg in history:
            role_label = "User" if msg["role"] == "user" else "Assistant"
            messages.append(HumanMessage(content=f"{role_label}: {msg['content']}"))

        response = llm.invoke(messages)
        new_summary = getattr(response, "content", "") or ""
        return new_summary.strip()

    except Exception as e:
        logger.error("summarize_conversation failed: %s", e)
        return previous_summary or ""


# ---- History pruning ----

def prune_old_history(session_id: str, keep_last: int = 10) -> None:
    """
    Delete all but the most recent `keep_last` messages for a session.
    """
    if not session_id:
        return

    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            DELETE FROM conversation_memory
            WHERE session_id = %s
            AND id NOT IN (
                SELECT id FROM conversation_memory
                WHERE session_id = %s
                ORDER BY id DESC
                LIMIT %s
            )
            """,
            (session_id, session_id, keep_last),
        )
        deleted = cur.rowcount
        conn.commit()
        if deleted > 0:
            logger.info("pruned_history session=%s deleted=%s kept=%s", session_id, deleted, keep_last)
    except Exception as e:
        logger.error("prune_old_history failed for session %s: %s", session_id, e)
    finally:
        if cur:
            try: cur.close()
            except Exception: pass
        if conn:
            release_connection(conn)


def maybe_summarize_and_prune(session_id: str, history: List[Dict[str, str]]) -> None:
    """
    Check if history exceeds 20 messages. If so:
    1. Generate a summary
    2. Store it in conversation_summary table
    3. Prune history to keep only last 10 messages
    """
    if len(history) <= 20:
        return

    logger.info("triggering_summarization session=%s history_len=%s", session_id, len(history))

    previous_summary = get_conversation_summary(session_id)
    new_summary = summarize_conversation(history, previous_summary)

    if new_summary:
        save_conversation_summary(session_id, new_summary)
        prune_old_history(session_id, keep_last=10)
