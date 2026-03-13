import os
import smtplib
from email.message import EmailMessage

from logger_config import logger


def _mask_email(value: str) -> str:
    try:
        if "@" not in value:
            return "***"
        local, domain = value.split("@", 1)
        local_masked = (local[:2] + "***") if local else "***"
        return f"{local_masked}@{domain}"
    except Exception:
        return "***"
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def send_email_via_gmail_oauth(
    to_email: str,
    subject: str,
    body: str
) -> bool:
    """Send email via Gmail API using stored OAuth token."""
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from db_config import get_connection, release_connection

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT access_token, refresh_token, token_expiry
            FROM google_oauth_tokens
            WHERE tenant_id = 'default'
            AND connected = TRUE
        """)
        row = cur.fetchone()
        cur.close()
        release_connection(conn)

        if not row:
            return False

        access_token, refresh_token, token_expiry = row

        from config import settings
        creds = Credentials(
            token=access_token,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
            scopes=["https://www.googleapis.com/auth/gmail.send"]
        )

        # Refresh if expired
        if creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            # Save new token
            conn2 = get_connection()
            cur2 = conn2.cursor()
            cur2.execute("""
                UPDATE google_oauth_tokens
                SET access_token = %s, updated_at = NOW()
                WHERE tenant_id = 'default'
            """, (creds.token,))
            conn2.commit()
            cur2.close()
            release_connection(conn2)

        service = build('gmail', 'v1', credentials=creds)

        message = MIMEMultipart()
        message['to'] = to_email
        message['subject'] = subject
        message.attach(MIMEText(body, 'plain'))

        raw = base64.urlsafe_b64encode(
            message.as_bytes()
        ).decode()

        service.users().messages().send(
            userId='me',
            body={'raw': raw}
        ).execute()

        print(f"[gmail_oauth] Email sent to {to_email}")
        return True

    except Exception as e:
        print(f"[gmail_oauth] Failed: {e}")
        return False


def send_email(to_email: str, subject: str, body: str) -> bool:
    """
    SMTP email sender.
    Reads configuration from environment variables:
      - SMTP_HOST
      - SMTP_PORT
      - SMTP_USER
      - SMTP_PASS
      - SMTP_FROM
    Returns True on success, False on failure.
    """
    
    # Try Gmail OAuth first
    if send_email_via_gmail_oauth(to_email, subject, body):
        return True
    
    # Fallback to SMTP
    print("[email] Gmail OAuth failed, falling back to SMTP")

    try:
        logger.info(
            "email_send_attempt to=%s subject_len=%s body_len=%s",
            _mask_email(to_email),
            len(subject or ""),
            len(body or ""),
        )

        host = os.getenv("SMTP_HOST")
        port_raw = os.getenv("SMTP_PORT", "587")
        user = os.getenv("SMTP_USER")
        password = os.getenv("SMTP_PASS")
        from_email = os.getenv("SMTP_FROM")

        if not host:
            raise RuntimeError("Missing SMTP_HOST in environment configuration.")
        if not user:
            raise RuntimeError("Missing SMTP_USER in environment configuration.")
        if not password:
            raise RuntimeError("Missing SMTP_PASS in environment configuration.")
        if not from_email:
            raise RuntimeError("Missing SMTP_FROM in environment configuration.")

        port = int(port_raw)

        msg = EmailMessage()
        msg["From"] = from_email
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(body)

        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)

        logger.info("email_send_success to=%s", _mask_email(to_email))
        return True
    except Exception as e:
        logger.error("email_send_failure to=%s error='%s'", _mask_email(to_email), e)
        return False

