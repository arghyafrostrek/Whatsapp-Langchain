import re
from typing import Any

from config import settings
from logger_config import logger


def _mask_db_url(url: str) -> str:
    if not url:
        return ""
    # masks postgresql://user:password@host...
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)


def init_conversation_table() -> None:
    """
    Create the conversation_memory table if it does not exist.
    Safe to call multiple times (idempotent).
    """
    conn = None
    cur = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_memory (
                id SERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS session_state (
                session_id TEXT PRIMARY KEY,
                state_data TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                conversation_summary TEXT DEFAULT NULL
            )
            """
        )
        try:
            cur.execute("ALTER TABLE session_state ADD COLUMN conversation_summary TEXT DEFAULT NULL;")
        except Exception:
            conn.rollback()
            pass

        # Add state tracking columns
        for col_stmt in [
            "ALTER TABLE session_state ADD COLUMN last_tool_used TEXT DEFAULT NULL;",
            "ALTER TABLE session_state ADD COLUMN action_pending BOOLEAN DEFAULT FALSE;",
            "ALTER TABLE session_state ADD COLUMN pending_action_data JSONB DEFAULT NULL;",
        ]:
            try:
                cur.execute(col_stmt)
            except Exception:
                conn.rollback()
                pass
            
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profile (
                session_id TEXT PRIMARY KEY,
                name TEXT,
                email TEXT,
                preferred_language TEXT,
                timezone TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_summary (
                session_id TEXT PRIMARY KEY,
                summary TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
            
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_logs (
                id SERIAL PRIMARY KEY,
                session_id TEXT NOT NULL,
                user_id TEXT DEFAULT '',
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                intent TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        # Safe migration: add intent column if table was created before it existed
        for col_stmt in [
            "ALTER TABLE chat_logs ADD COLUMN intent TEXT DEFAULT '';",
            "ALTER TABLE chat_logs ADD COLUMN user_id TEXT DEFAULT '';",
        ]:
            try:
                cur.execute(col_stmt)
            except Exception:
                conn.rollback()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id SERIAL PRIMARY KEY,
                session_id TEXT,
                name TEXT,
                email TEXT,
                phone TEXT,
                interest TEXT,
                follow_up_sent BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id SERIAL PRIMARY KEY,
                filename TEXT,
                category TEXT,
                chunk_text TEXT,
                embedding TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
            """
        )

        cur.execute("""
            CREATE TABLE IF NOT EXISTS 
            google_oauth_tokens (
                id SERIAL PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                email TEXT,
                access_token TEXT,
                refresh_token TEXT,
                token_expiry TIMESTAMP,
                scope TEXT,
                connected BOOLEAN 
                    DEFAULT TRUE,
                created_at TIMESTAMP 
                    DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP 
                    DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Add unique constraint on tenant_id
        try:
            cur.execute("""
                ALTER TABLE google_oauth_tokens
                ADD CONSTRAINT 
                unique_tenant_oauth
                UNIQUE (tenant_id)
            """)
        except Exception:
            conn.rollback()

        conn.commit()
    except Exception as e:
        logger.error("Failed to initialize database tables: %s", e)
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if conn is not None:
            release_connection(conn)


_db_pool = None

def get_connection() -> Any:
    """
    Return a DB connection from the connection pool.
    Initializes the pool on the first call.
    """
    global _db_pool
    
    if _db_pool is None:
        database_url = settings.DATABASE_URL
        if not database_url:
            logger.error("Database connection failed: Missing DATABASE_URL.")
            raise ValueError("Missing DATABASE_URL in environment configuration.")

        masked_url = _mask_db_url(database_url)
        logger.info("Initializing connection pool to: %s", masked_url)

        try:
            import psycopg2  # type: ignore
            from psycopg2 import pool
        except ImportError as e:
            logger.error("psycopg2 driver not installed.")
            raise RuntimeError("Import error: psycopg2 driver is required.") from e

        try:
            _db_pool = pool.SimpleConnectionPool(2, 10, database_url)
            if not _db_pool:
                raise RuntimeError("Failed to create SimpleConnectionPool.")
        except Exception as e:
            logger.error("Connection pool creation failed for %s. Exception: %s", masked_url, e)
            raise RuntimeError(f"Pool creation failure: {e}") from e

    try:
        return _db_pool.getconn()
    except Exception as e:
        logger.error("Failed to get connection from pool. Exception: %s", e)
        raise RuntimeError(f"Connection pool getconn failure: {e}") from e


def release_connection(conn: Any) -> None:
    """
    Return a DB connection back to the connection pool.
    """
    global _db_pool
    if _db_pool is not None and conn is not None:
        try:
            _db_pool.putconn(conn)
        except Exception as e:
            logger.warning("Failed to release connection to pool: %s", e)
