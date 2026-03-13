"""
Slack notification service for Frosty.
Multi-tenant: each tenant has their
own bot token stored in DB.
3 notification types:
  1. New lead captured
  2. Meeting booked
  3. Email sent to prospect
"""
import httpx
from datetime import datetime
from logger_config import logger


SLACK_API = "https://slack.com/api/chat.postMessage"


def _get_slack_creds(
    tenant_id: str
) -> tuple:
    """
    Fetch bot_token + channel_id
    from DB for this tenant.
    Returns (token, channel_id)
    or (None, None) if not connected.
    """
    try:
        from session_store import (
            get_connection,
            release_connection
        )
        conn = get_connection()
        cur  = conn.cursor()
        cur.execute(
            """
            SELECT bot_token, channel_id
            FROM slack_tokens
            WHERE tenant_id = %s
            """,
            (tenant_id,)
        )
        row = cur.fetchone()
        cur.close()
        release_connection(conn)
        if row:
            return row[0], row[1]
        return None, None
    except Exception as e:
        logger.error(
            "slack _get_creds failed: %s",
            e
        )
        return None, None


def _post(
    tenant_id: str,
    blocks: list,
    text: str
) -> None:
    """
    POST a Slack message.
    Never raises — always safe to call.
    """
    token, channel = _get_slack_creds(
        tenant_id
    )

    if not token or not channel:
        logger.info(
            "slack_notifier: not connected "
            "for tenant=%s — skipping",
            tenant_id
        )
        return

    try:
        resp = httpx.post(
            SLACK_API,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "channel": channel,
                "text": text,
                "blocks": blocks,
            },
            timeout=8,
        )
        data = resp.json()
        if not data.get("ok"):
            logger.error(
                "slack post error: %s",
                data.get("error", "unknown")
            )
        else:
            logger.info(
                "slack sent OK tenant=%s",
                tenant_id
            )
    except Exception as e:
        logger.error(
            "slack post exception: %s", e
        )


def _now() -> str:
    return datetime.now().strftime(
        "%d %b %Y at %I:%M %p"
    )


def _trim(text: str, limit=200) -> str:
    if not text:
        return "—"
    return (
        text[:limit] + "…"
        if len(text) > limit
        else text
    )


# ────────────────────────────────────
# 1. NEW LEAD CAPTURED
# ────────────────────────────────────

def notify_new_lead(
    tenant_id: str,
    name: str = None,
    email: str = None,
    phone: str = None,
    interest: str = None,
    query: str = None,
) -> None:
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "🧊 New Lead Captured",
                "emoji": True
            }
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Name*\n{name or 'Unknown'}"
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Email*\n{email or 'Not provided'}"
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Phone*\n{phone or 'Not provided'}"
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Interest*\n{interest or 'General enquiry'}"
                },
            ]
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Their Query*\n"
                    f"_{_trim(query)}_"
                )
            }
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"⏱ {_now()} · via Frosty"
                }
            ]
        },
        {"type": "divider"}
    ]
    _post(
        tenant_id, blocks,
        f"New lead: {name or 'Unknown'}"
        f" ({email or 'no email'})"
    )


# ────────────────────────────────────
# 2. MEETING BOOKED
# ────────────────────────────────────

def notify_meeting_booked(
    tenant_id: str,
    name: str = None,
    email: str = None,
    meeting_time: str = None,
    meet_link: str = None,
    summary: str = None,
) -> None:
    link_text = (
        f"<{meet_link}|Join Google Meet>"
        if meet_link else "—"
    )

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📅 Meeting Booked",
                "emoji": True
            }
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Name*\n{name or 'Unknown'}"
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Email*\n{email or 'Not provided'}"
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Meeting Time*\n{meeting_time or '—'}"
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Google Meet*\n{link_text}"
                },
            ]
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Subject*\n"
                    f"{summary or 'Meeting with prospect'}"
                )
            }
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"⏱ {_now()} · via Frosty"
                }
            ]
        },
        {"type": "divider"}
    ]
    _post(
        tenant_id, blocks,
        f"Meeting booked: {name or 'Unknown'}"
        f" @ {meeting_time or '—'}"
    )


# ────────────────────────────────────
# 3. EMAIL SENT TO PROSPECT
# ────────────────────────────────────

def notify_email_sent(
    tenant_id: str,
    name: str = None,
    email: str = None,
    subject: str = None,
    query: str = None,
    interest: str = None,
) -> None:
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "📧 Email Sent to Prospect",
                "emoji": True
            }
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Name*\n{name or 'Unknown'}"
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Email Sent To*\n{email or 'Not provided'}"
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Subject*\n{subject or '—'}"
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Interest*\n{interest or 'General enquiry'}"
                },
            ]
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*Original Query*\n"
                    f"_{_trim(query)}_"
                )
            }
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"⏱ {_now()} · via Frosty"
                }
            ]
        },
        {"type": "divider"}
    ]
    _post(
        tenant_id, blocks,
        f"Email sent to {name or 'Unknown'}"
        f" ({email or 'no email'})"
    )
