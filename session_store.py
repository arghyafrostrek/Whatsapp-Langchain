from __future__ import annotations

import json
from typing import List, Dict, Any

from db_config import get_connection, release_connection
from logger_config import logger
from services.slack_notifier import notify_new_lead


# Max messages passed to LLM context.
# All messages are still stored in DB.
_LLM_HISTORY_LIMIT = 20

def get_session_history(session_id: str, tenant_id: str = "default") -> List[dict]:
    """
    Retrieve the last messages for a session from the database,
    returned in chronological (oldest-first) order.
    """
    if not session_id:
        return []

    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT role, content
            FROM (
                SELECT id, role, content
                FROM conversation_memory
                WHERE session_id = %s
                ORDER BY id DESC
                LIMIT %s
            ) recent
            ORDER BY id ASC
            """,
            (session_id, _LLM_HISTORY_LIMIT),
        )
        rows = cur.fetchall()
        return [
            {"role": r[0], "content": r[1]}
            for r in rows
        ]
    except Exception as e:
        logger.error("get_session_history failed for session %s: %s", session_id, e)
        return []
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            release_connection(conn)


def save_message(
    session_id: str,
    role: str,
    content: str,
    user_email: str = None,
    tenant_id: str = "default"
) -> None:
    """
    Persist a single message to the DB.
    Tags message with email if provided
    or if email exists in session state.
    ALL messages kept forever in DB.
    LLM only sees last 20 via
    get_session_history.
    """
    if not session_id or not content:
        return

    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Deduplication check: do not save if same message within last 5 seconds
        cur.execute(
            """
            SELECT id FROM conversation_memory
            WHERE session_id = %s
            AND role = %s
            AND content = %s
            AND created_at > NOW() - INTERVAL '5 seconds'
            """,
            (session_id, role, content)
        )
        if cur.fetchone():
            return

        # Auto-fetch email from session
        # state if not explicitly passed
        if not user_email:
            try:
                state = get_session_state(
                    session_id
                )
                user_email = (
                    state.get("recipient") or
                    state.get(
                        "intent", {}
                    ).get("email_address")
                )
            except Exception:
                pass

        cur.execute(
            """
            INSERT INTO conversation_memory
            (session_id, role, content,
             user_email)
            VALUES (%s, %s, %s, %s)
            """,
            (session_id, role,
             content, user_email),
        )
        conn.commit()

    except Exception as e:
        logger.error(
            "save_message failed "
            "session=%s: %s",
            session_id, e
        )
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            release_connection(conn)


def get_full_history(
    session_id: str,
    tenant_id: str = "default"
) -> List[dict]:
    """
    Retrieve ALL messages for a session.
    Used for audit logs and admin panel.
    Never limited.
    """
    if not session_id:
        return []

    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT role, content,
                   created_at, user_email
            FROM conversation_memory
            WHERE session_id = %s
            ORDER BY id ASC
            """,
            (session_id,),
        )
        rows = cur.fetchall()
        return [
            {
                "role": r[0],
                "content": r[1],
                "timestamp": str(r[2]),
                "user_email": r[3]
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(
            "get_full_history failed "
            "session=%s: %s",
            session_id, e
        )
        return []
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            release_connection(conn)


def get_sessions_by_email(
    email: str,
    tenant_id: str = "default"
) -> List[dict]:
    """
    Get all sessions for a given email.
    Used by admin panel.
    """
    if not email:
        return []

    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                session_id,
                COUNT(*) as message_count,
                MIN(created_at) as first_seen,
                MAX(created_at) as last_seen
            FROM conversation_memory
            WHERE user_email = %s
            GROUP BY session_id
            ORDER BY last_seen DESC
            """,
            (email,),
        )
        rows = cur.fetchall()
        return [
            {
                "session_id": r[0],
                "message_count": r[1],
                "first_seen": str(r[2]),
                "last_seen": str(r[3]),
                "email": email
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(
            "get_sessions_by_email "
            "failed email=%s: %s",
            email, e
        )
        return []
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            release_connection(conn)


def get_all_sessions_summary(tenant_id: str = "default") -> List[dict]:
    """
    Get all sessions overview.
    Used by admin panel.
    """
    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
                session_id,
                MAX(user_email) as user_email,
                COUNT(*) as message_count,
                MIN(created_at) as first_seen,
                MAX(created_at) as last_seen
            FROM conversation_memory
            GROUP BY session_id
            ORDER BY last_seen DESC
            LIMIT 500
            """,
        )
        rows = cur.fetchall()
        return [
            {
                "session_id": r[0],
                "email": r[1] or "Anonymous",
                "message_count": r[2],
                "first_seen": str(r[3]),
                "last_seen": str(r[4])
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(
            "get_all_sessions_summary "
            "failed: %s", e
        )
        return []
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            release_connection(conn)

def get_session_state(session_id: str, tenant_id: str = "default") -> Dict[str, Any]:
    """
    Retrieve the serialized dictionary state associated with the session.
    """
    if not session_id:
        return {}

    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT state_data
            FROM session_state
            WHERE session_id = %s
            """,
            (session_id,),
        )
        row = cur.fetchone()
        if row and row[0]:
            return json.loads(row[0])
        return {}
    except Exception as e:
        logger.error("get_session_state failed for session %s: %s", session_id, e)
        return {}
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            release_connection(conn)

def save_session_state(session_id: str, state_dict: Dict[str, Any], tenant_id: str = "default") -> None:
    """
    Persist the python dict state into the DB associated with the session.
    """
    if not session_id:
        return
        
    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        state_json = json.dumps(state_dict)
        cur.execute(
            """
            INSERT INTO session_state (session_id, state_data)
            VALUES (%s, %s)
            ON CONFLICT (session_id) 
            DO UPDATE SET state_data = EXCLUDED.state_data, updated_at = CURRENT_TIMESTAMP
            """,
            (session_id, state_json),
        )
        conn.commit()
    except Exception as e:
        logger.error("save_session_state failed for session %s: %s", session_id, e)
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            release_connection(conn)

def get_conversation_summary(session_id: str, tenant_id: str = "default") -> str:
    """
    Retrieve the running conversation summary.
    """
    if not session_id:
        return ""

    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT conversation_summary
            FROM session_state
            WHERE session_id = %s
            """,
            (session_id,),
        )
        row = cur.fetchone()
        if row and row[0]:
            return row[0]
        return ""
    except Exception as e:
        logger.error("get_conversation_summary failed for session %s: %s", session_id, e)
        return ""
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            release_connection(conn)

def update_conversation_summary(session_id: str, new_summary: str, tenant_id: str = "default") -> None:
    """
    Update the running conversation summary in the database safely.
    """
    if not session_id or not new_summary:
        return
        
    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE session_state
            SET conversation_summary = %s, updated_at = CURRENT_TIMESTAMP
            WHERE session_id = %s
            """,
            (new_summary, session_id),
        )
        conn.commit()
    except Exception as e:
        logger.error("update_conversation_summary failed for session %s: %s", session_id, e)
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            release_connection(conn)

def get_user_profile(session_id: str, tenant_id: str = "default") -> dict:
    """
    Retrieve the user profile for the session.
    """
    if not session_id:
        return {}

    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT name, email, preferred_language, timezone
            FROM user_profile
            WHERE session_id = %s
            """,
            (session_id,),
        )
        row = cur.fetchone()
        if row:
            return {
                "name": row[0],
                "email": row[1],
                "preferred_language": row[2],
                "timezone": row[3]
            }
        return {}
    except Exception as e:
        logger.error("get_user_profile failed for session %s: %s", session_id, e)
        return {}
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            release_connection(conn)

def update_user_profile(session_id: str, profile_data: dict, tenant_id: str = "default") -> None:
    """
    Update the user profile for the session safely.
    """
    if not session_id or not profile_data:
        return
        
    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        # Get existing to merge
        cur.execute(
            "SELECT name, email, preferred_language, timezone FROM user_profile WHERE session_id = %s", 
            (session_id,)
        )
        row = cur.fetchone()
        existing = {"name": None, "email": None, "preferred_language": None, "timezone": None}
        if row:
            existing = {"name": row[0], "email": row[1], "preferred_language": row[2], "timezone": row[3]}
        
        # Merge
        for k, v in profile_data.items():
            if v is not None:
                existing[k] = v
                
        cur.execute(
            """
            INSERT INTO user_profile (session_id, name, email, preferred_language, timezone)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (session_id) 
            DO UPDATE SET 
                name = EXCLUDED.name,
                email = EXCLUDED.email,
                preferred_language = EXCLUDED.preferred_language,
                timezone = EXCLUDED.timezone,
                updated_at = CURRENT_TIMESTAMP
            """,
            (session_id, existing.get("name"), existing.get("email"), existing.get("preferred_language"), existing.get("timezone")),
        )
        conn.commit()

        try:
            notify_new_lead(
                tenant_id="default",  # or get tenant from context
                name=existing.get("name"),
                email=existing.get("email"),
                phone=None,
                interest=None,
                query="Profile extracted from conversation",
            )
        except Exception as _se:
            logger.warning(
                "slack lead notify: %s",
                _se
            )
            
    except Exception as e:
        logger.error("update_user_profile failed for session %s: %s", session_id, e)
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            release_connection(conn)


def set_pending_action(session_id: str, tool_name: str, action_data: Dict[str, Any], tenant_id: str = "default") -> None:
    """
    Store a pending action for the session.
    Sets action_pending=True, last_tool_used, and pending_action_data.
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
            UPDATE session_state
            SET last_tool_used = %s,
                action_pending = TRUE,
                pending_action_data = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE session_id = %s
            """,
            (tool_name, json.dumps(action_data), session_id),
        )
        conn.commit()
        logger.info("set_pending_action session=%s tool=%s", session_id, tool_name)
    except Exception as e:
        logger.error("set_pending_action failed for session %s: %s", session_id, e)
    finally:
        if cur:
            try: cur.close()
            except Exception: pass
        if conn:
            release_connection(conn)


def get_pending_action(session_id: str, tenant_id: str = "default") -> Dict[str, Any]:
    """
    Retrieve pending action data for the session.
    Returns dict with 'tool', 'pending', and 'data' keys.
    """
    if not session_id:
        return {"tool": None, "pending": False, "data": None}

    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT last_tool_used, action_pending, pending_action_data
            FROM session_state
            WHERE session_id = %s
            """,
            (session_id,),
        )
        row = cur.fetchone()
        if row:
            data = row[2]
            if isinstance(data, str):
                data = json.loads(data)
            return {
                "tool": row[0],
                "pending": bool(row[1]) if row[1] is not None else False,
                "data": data,
            }
        return {"tool": None, "pending": False, "data": None}
    except Exception as e:
        logger.error("get_pending_action failed for session %s: %s", session_id, e)
        return {"tool": None, "pending": False, "data": None}
    finally:
        if cur:
            try: cur.close()
            except Exception: pass
        if conn:
            release_connection(conn)


def clear_pending_action(session_id: str, tenant_id: str = "default") -> None:
    """Clear the pending action for the session."""
    if not session_id:
        return

    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE session_state
            SET action_pending = FALSE,
                pending_action_data = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE session_id = %s
            """,
            (session_id,),
        )
        conn.commit()
        logger.info("clear_pending_action session=%s", session_id)
    except Exception as e:
        logger.error("clear_pending_action failed for session %s: %s", session_id, e)
    finally:
        if cur:
            try: cur.close()
            except Exception: pass
        if conn:
            release_connection(conn)
