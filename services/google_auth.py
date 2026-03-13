import os
import json
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from logger_config import logger

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.send",
]

CLIENT_CONFIG = {
    "web": {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv(
            "GOOGLE_CLIENT_SECRET"
        ),
        "redirect_uris": [
            os.getenv(
                "GOOGLE_REDIRECT_URI",
                "http://localhost:8000"
                "/auth/google/callback"
            )
        ],
        "auth_uri": (
            "https://accounts.google.com/o/oauth2/auth"
        ),
        "token_uri": (
            "https://oauth2.googleapis.com/token"
        ),
    }
}


def get_oauth_flow(state: str = None) -> Flow:
    """Create and return OAuth flow."""
    flow = Flow.from_client_config(
        CLIENT_CONFIG,
        scopes=SCOPES,
        state=state
    )
    flow.redirect_uri = os.getenv(
        "GOOGLE_REDIRECT_URI",
        "http://localhost:8000/auth/google/callback"
    )
    return flow


def get_authorization_url() -> tuple[str, str]:
    """
    Generate Google OAuth authorization URL.
    Returns (url, state) tuple.
    """
    flow = get_oauth_flow()
    url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent"
    )
    return url, state


def exchange_code_for_tokens(
    code: str,
    state: str
) -> dict:
    """
    Exchange authorization code for tokens.
    Returns token dict.
    """
    flow = get_oauth_flow(state=state)
    flow.fetch_token(code=code)
    credentials = flow.credentials
    
    return {
        "access_token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_expiry": (
            datetime.utcnow() + timedelta(hours=1)
        ).isoformat(),
        "scope": " ".join(SCOPES)
    }


def get_user_email(access_token: str) -> str:
    """Get Google account email from token."""
    try:
        from google.oauth2.credentials import (
            Credentials
        )
        creds = Credentials(token=access_token)
        service = build(
            "oauth2", "v2",
            credentials=creds
        )
        info = service.userinfo().get().execute()
        return info.get("email", "")
    except Exception as e:
        logger.error(
            "get_user_email failed: %s", e
        )
        return ""


def save_tokens(
    tenant_id: str,
    tokens: dict,
    email: str
) -> bool:
    """Save OAuth tokens to database."""
    try:
        from db_config import (
            get_connection, release_connection
        )
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO google_oauth_tokens
                (tenant_id, email, access_token,
                 refresh_token, token_expiry,
                 scope, connected, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,TRUE,
                        CURRENT_TIMESTAMP)
                ON CONFLICT (tenant_id)
                DO UPDATE SET
                    email = EXCLUDED.email,
                    access_token = 
                        EXCLUDED.access_token,
                    refresh_token = 
                        EXCLUDED.refresh_token,
                    token_expiry = 
                        EXCLUDED.token_expiry,
                    scope = EXCLUDED.scope,
                    connected = TRUE,
                    updated_at = 
                        CURRENT_TIMESTAMP
            """, (
                tenant_id,
                email,
                tokens["access_token"],
                tokens.get("refresh_token"),
                tokens["token_expiry"],
                tokens["scope"]
            ))
            conn.commit()
            cur.close()
            return True
        finally:
            release_connection(conn)
    except Exception as e:
        logger.error(
            "save_tokens failed: %s", e
        )
        return False


def get_calendar_service(tenant_id: str):
    """
    Build Google Calendar service for tenant.
    Automatically refreshes token if expired.
    Returns service or None if not connected.
    """
    try:
        from db_config import (
            get_connection, release_connection
        )
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT access_token,
                       refresh_token,
                       token_expiry
                FROM google_oauth_tokens
                WHERE tenant_id = %s
                AND connected = TRUE
            """, (tenant_id,))
            row = cur.fetchone()
            cur.close()
        finally:
            release_connection(conn)
        
        if not row:
            logger.warning(
                "No OAuth token for "
                "tenant=%s", tenant_id
            )
            return None
        
        access_token = row[0]
        refresh_token = row[1]
        token_expiry = row[2]
        
        # Build credentials
        creds = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri=(
                "https://oauth2.googleapis"
                ".com/token"
            ),
            client_id=os.getenv(
                "GOOGLE_CLIENT_ID"
            ),
            client_secret=os.getenv(
                "GOOGLE_CLIENT_SECRET"
            ),
            scopes=SCOPES
        )
        
        # Refresh if expired
        # Handle both string and datetime
        if token_expiry:
            if isinstance(token_expiry, str):
                token_expiry = datetime.fromisoformat(
                    token_expiry.replace("Z", "")
                )
            # Strip timezone if present
            if hasattr(token_expiry, 'tzinfo') \
                    and token_expiry.tzinfo:
                from datetime import timezone
                token_expiry = token_expiry.replace(
                    tzinfo=None
                )
            if datetime.utcnow() >= token_expiry:
                from google.auth.transport.requests\
                    import Request
                creds.refresh(Request())
            
            # Save refreshed token
                save_tokens(
                    tenant_id,
                    {
                        "access_token": creds.token,
                        "refresh_token": (
                            creds.refresh_token
                        ),
                        "token_expiry": (
                            datetime.utcnow() +
                            timedelta(hours=1)
                        ).isoformat(),
                        "scope": " ".join(SCOPES)
                    },
                    ""
                )
        
        from datetime import datetime as _dt
        if hasattr(creds, 'expiry') and creds.expiry:
            logger.info(
                "token_expiry=%s now=%s expired=%s",
                creds.expiry,
                _dt.utcnow(),
                creds.expired
            )
        if hasattr(creds, 'expired') and creds.expired:
            logger.warning(
                "google_token_expired "
                "tenant=%s attempting_refresh",
                tenant_id
            )
            try:
                from google.auth.transport.requests \
                    import Request
                creds.refresh(Request())
                logger.info(
                    "token_refreshed_successfully "
                    "tenant=%s",
                    tenant_id
                )
            except Exception as e:
                logger.error(
                    "token_refresh_failed "
                    "tenant=%s error=%s",
                    tenant_id, e
                )
                return None

        logger.info(
            "calendar_service_created "
            "tenant=%s token_valid=%s",
            tenant_id,
            True
        )
        return build(
            "calendar", "v3",
            credentials=creds
        )
    
    except Exception as e:
        logger.error(
            "get_calendar_service failed "
            "tenant=%s error=%s",
            tenant_id, e
        )
        return None


def is_calendar_connected(
    tenant_id: str
) -> bool:
    """Check if tenant has connected calendar."""
    try:
        from db_config import (
            get_connection, release_connection
        )
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT connected, email
                FROM google_oauth_tokens
                WHERE tenant_id = %s
            """, (tenant_id,))
            row = cur.fetchone()
            cur.close()
        finally:
            release_connection(conn)
        
        if row:
            return bool(row[0])
        return False
    except Exception as e:
        logger.error(
            "is_calendar_connected "
            "failed: %s", e
        )
        return False
