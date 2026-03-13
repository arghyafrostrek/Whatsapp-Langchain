"""
User profile memory module.
Extracts name/email from messages,
stores to DB, and tracks whether
bot has already asked for them
to avoid asking repeatedly.
"""
import re
from typing import Optional
from logger_config import logger

_NAME_PATTERN = re.compile(
    r"(?i)(?:my name is|i am|i'm|"
    r"call me|this is)\s+"
    r"([a-zA-Z][a-zA-Z\s]{1,30})"
)
_EMAIL_PATTERN = re.compile(
    r"[a-zA-Z0-9._%+-]+"
    r"@[a-zA-Z0-9.-]+"
    r"\.[a-zA-Z]{2,}"
)


def get_user_profile(
    session_id: str
) -> dict:
    """Get profile from DB."""
    if not session_id:
        return {}
    try:
        from session_store import (
            get_user_profile as _db_get
        )
        return _db_get(session_id)
    except Exception as e:
        logger.error(
            "get_user_profile failed "
            "session=%s: %s",
            session_id, e
        )
        return {}


def update_user_profile(
    session_id: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> None:
    """Update profile in DB."""
    if not session_id:
        return
    updates: dict = {}
    if name is not None:
        updates["name"] = name
    if email is not None:
        updates["email"] = email
    if not updates:
        return
    try:
        from session_store import (
            update_user_profile as _db_update
        )
        _db_update(session_id, updates)
        logger.info(
            "profile_updated session=%s "
            "keys=%s",
            session_id, list(updates.keys())
        )
    except Exception as e:
        logger.error(
            "update_user_profile failed "
            "session=%s: %s",
            session_id, e
        )


def extract_and_save_profile(
    session_id: str,
    message: str
) -> dict:
    """
    Detect name/email from user message.
    Saves to DB if found.
    Returns dict of what was extracted.
    """
    if not session_id or not message:
        return {}

    updates: dict = {}

    name_match = _NAME_PATTERN.search(
        message
    )
    if name_match:
        raw = name_match.group(1)
        name = " ".join(
            raw.strip().split()[:2]
        ).strip(".,!? ")
        if name and len(name) > 1:
            updates["name"] = name

    email_match = _EMAIL_PATTERN.search(
        message
    )
    if email_match:
        updates["email"] = (
            email_match.group(0)
            .strip().lower()
        )

    if updates:
        update_user_profile(
            session_id, **updates
        )
        logger.info(
            "profile_extracted "
            "session=%s keys=%s",
            session_id,
            list(updates.keys())
        )

    return updates


def should_ask_name(
    session_id: str,
    message_count: int
) -> bool:
    """
    Returns True if bot should ask
    for user's name.
    Triggers at turn 3 if name unknown.
    """
    if message_count != 3:
        return False
    try:
        profile = get_user_profile(
            session_id
        )
        return not profile.get("name")
    except Exception:
        return False


def should_ask_email(
    session_id: str,
    message_count: int
) -> bool:
    """
    Returns True if bot should offer
    to send info to email.
    Triggers at turn 5 if email unknown.
    """
    if message_count != 5:
        return False
    try:
        profile = get_user_profile(
            session_id
        )
        return not profile.get("email")
    except Exception:
        return False
