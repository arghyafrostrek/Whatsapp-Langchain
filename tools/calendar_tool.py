from datetime import datetime, timedelta
from logger_config import logger

TIMEZONE = "Asia/Kolkata"
TIMEZONE_OFFSET = "+05:30"
DEFAULT_MEETING_DURATION = 60  # minutes
DEFAULT_SEARCH_HOURS = 8  # hours to search


def check_availability(
    tenant_id: str,
    start_time: str,
    end_time: str
) -> dict:
    """
    Check if time slot is free on
    company calendar.

    Returns:
        available: bool
        conflicts: list of event names
    """
    try:
        from services.google_auth import (
            get_calendar_service
        )
        service = get_calendar_service(tenant_id)
        # ── ALIAS ── some callers pass duration=
        logger.info(
            "check_availability_start "
            "tenant=%s start=%s end=%s",
            tenant_id, start_time, end_time
        )
        
        from datetime import datetime, timezone
        import pytz
        
        IST = pytz.timezone("Asia/Kolkata")
        
        # Parse the incoming start/end times
        # They come in as "2026-03-09T11:00:00"
        
        def to_utc_iso(dt_str: str) -> str:
            """Convert IST datetime string to UTC ISO."""
            # Parse naive datetime
            naive_dt = datetime.fromisoformat(dt_str)
            
            # Localize to IST
            ist_dt = IST.localize(naive_dt)
            
            # Convert to UTC
            utc_dt = ist_dt.astimezone(timezone.utc)
            
            # Return RFC3339 format for Google API
            return utc_dt.strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        
        body = {
            "timeMin": to_utc_iso(start_time),
            "timeMax": to_utc_iso(end_time),
            "timeZone": "Asia/Kolkata",
            "items": [{"id": "primary"}],
        }
        
        result = service.freebusy().query(
            body=body
        ).execute()

        logger.info(
            "freebusy_raw_result: %s",
            result
        )
        
        busy_slots = result.get(
            "calendars", {}
        ).get("primary", {}).get("busy", [])
        
        logger.info(
            "busy_slots found: %s",
            busy_slots
        )
        
        conflicts = [
            "Busy" for _ in busy_slots
        ]

        return {
            "available": len(busy_slots) == 0,
            "conflicts": conflicts,
            "error": None
        }

    except Exception as e:
        logger.error(
            "check_availability failed: %s", e
        )
        return {
            "available": False,
            "conflicts": [],
            "error": str(e)
        }


def find_free_slots(
    tenant_id: str,
    date_str: str,
    duration_minutes: int = DEFAULT_MEETING_DURATION,
    num_slots: int = 3,
    # ── ALIAS ── some callers pass duration=
    # instead of duration_minutes=
    # Accept both to avoid TypeError crashes
    duration: int = None,
) -> list:
    """
    Find multiple free slots on a given date.

    Returns list of dicts:
    [
      {
        "start": "09:00 AM",
        "end": "10:00 AM",
        "start_display": "9:00 AM",
        "end_display": "10:00 AM",
        "start_iso": "2026-03-08T09:00:00",
        "end_iso":   "2026-03-08T10:00:00"
      }
    ]
    """
    # Resolve duration alias
    if duration is not None:
        duration_minutes = duration

    try:
        from services.google_auth import (
            get_calendar_service
        )
        
        logger.info(
            "find_free_slots_start "
            "tenant=%s date=%s duration=%s",
            tenant_id, date_str, duration
        )
        
        service = get_calendar_service(tenant_id)
        if not service:
            return []

        import pytz
        from datetime import timezone as _tz
        IST = pytz.timezone("Asia/Kolkata")

        def to_utc_iso(dt_str: str) -> str:
            naive_dt = datetime.fromisoformat(
                dt_str
            )
            ist_dt = IST.localize(naive_dt)
            utc_dt = ist_dt.astimezone(_tz.utc)
            return utc_dt.strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

        # Search from 9 AM to 6 PM
        search_start = datetime.fromisoformat(
            f"{date_str}T09:00:00"
        )
        search_end = datetime.fromisoformat(
            f"{date_str}T18:00:00"
        )

        # When building slot start/end for
        # freebusy query inside find_free_slots
        # use the same to_utc_iso() function
        body = {
            "timeMin": to_utc_iso(search_start.isoformat()),
            "timeMax": to_utc_iso(search_end.isoformat()),
            "timeZone": "Asia/Kolkata",
            "items": [{"id": "primary"}],
        }

        result = service.freebusy().query(
            body=body
        ).execute()

        busy_slots = result.get("calendars", {}).get("primary", {}).get("busy", [])

        # Build busy periods
        busy = []
        for bs in busy_slots:
            s = bs.get("start")
            e = bs.get("end")
            if s and e:
                s_utc = datetime.fromisoformat(s.replace("Z", "+00:00"))
                e_utc = datetime.fromisoformat(e.replace("Z", "+00:00"))
                busy.append((
                    s_utc.astimezone(IST).replace(tzinfo=None),
                    e_utc.astimezone(IST).replace(tzinfo=None)
                ))

        busy.sort(key=lambda x: x[0])

        # Find free slots
        free_slots = []
        current = search_start
        dur = timedelta(minutes=duration_minutes)

        for busy_start, busy_end in busy:
            while (
                current + dur <= busy_start
                and len(free_slots) < num_slots
            ):
                slot_end = current + dur
                logger.info(
                    "checking_slot start=%s end=%s",
                    current, slot_end
                )
                free_slots.append(
                    _make_slot(current, slot_end)
                )
                current = slot_end
            current = max(current, busy_end)

        # Check remaining time after last event
        while (
            current + dur <= search_end
            and len(free_slots) < num_slots
        ):
            slot_end = current + dur
            logger.info(
                "checking_slot start=%s end=%s",
                current, slot_end
            )
            free_slots.append(
                _make_slot(current, slot_end)
            )
            current = slot_end

        return free_slots

    except Exception as e:
        logger.error(
            "find_free_slots failed: %s", e
        )
        return []


def _make_slot(
    start: datetime,
    end: datetime
) -> dict:
    """Build a consistent slot dict."""
    return {
        # 12-hour format for display
        "start": start.strftime("%I:%M %p"),
        "end": end.strftime("%I:%M %p"),
        # Friendly display (no leading zero)
        "start_display": start.strftime("%I:%M %p").lstrip("0"),
        "end_display": end.strftime("%I:%M %p").lstrip("0"),
        # ISO for further processing
        "start_iso": start.isoformat(),
        "end_iso": end.isoformat(),
    }


def create_meeting(
    tenant_id: str,
    title: str,
    description: str,
    start_iso: str,
    end_iso: str,
    attendee_email: str
) -> dict:
    """
    Create Google Calendar event with
    Google Meet link and send invite
    to attendee email.

    Returns:
        success: bool
        meet_link: str
        event_link: str
        error: str
    """
    try:
        from services.google_auth import (
            get_calendar_service
        )
        service = get_calendar_service(tenant_id)
        if not service:
            return {
                "success": False,
                "meet_link": None,
                "event_link": None,
                "error": "Calendar not connected"
            }

        event = {
            "summary": title,
            "description": description,
            "start": {
                "dateTime": start_iso,
                "timeZone": "Asia/Kolkata",
            },
            "end": {
                "dateTime": end_iso,
                "timeZone": "Asia/Kolkata",
            },
            "attendees": [
                {"email": attendee_email}
            ],
            "conferenceData": {
                "createRequest": {
                    "requestId": (
                        f"frosty-{start_iso}"
                        .replace(":", "-")
                        .replace("T", "-")
                    ),
                    "conferenceSolutionKey": {
                        "type": "hangoutsMeet"
                    }
                }
            },
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {
                        "method": "email",
                        "minutes": 60
                    },
                    {
                        "method": "popup",
                        "minutes": 15
                    }
                ]
            },
            "guestsCanModifyEvent": False,
            "guestsCanSeeOtherGuests": False,
            "sendUpdates": "all"
        }

        created = service.events().insert(
            calendarId="primary",
            body=event,
            conferenceDataVersion=1,
            sendUpdates="all"
        ).execute()

        # Extract Meet link
        meet_link = None
        conference = created.get(
            "conferenceData", {}
        )
        entry_points = conference.get(
            "entryPoints", []
        )
        for ep in entry_points:
            if ep.get("entryPointType") == "video":
                meet_link = ep.get("uri")
                break

        event_link = created.get("htmlLink")

        logger.info(
            "meeting_created title=%s "
            "attendee=%s meet=%s",
            title, attendee_email, meet_link
        )

        return {
            "success": True,
            "meet_link": meet_link,
            "event_link": event_link,
            "event_id": created.get("id", ""),
            "error": None
        }

    except Exception as e:
        logger.error(
            "create_meeting failed: %s", e
        )
        return {
            "success": False,
            "meet_link": None,
            "event_link": None,
            "error": str(e)
        }


def get_todays_events(tenant_id: str) -> list:
    """Get all events for today."""
    try:
        from services.google_auth import (
            get_calendar_service
        )
        service = get_calendar_service(tenant_id)
        if not service:
            return []

        today = datetime.now().strftime("%Y-%m-%d")
        start = f"{today}T00:00:00"
        end = f"{today}T23:59:59"

        result = service.events().list(
            calendarId="primary",
            timeMin=start + TIMEZONE_OFFSET,
            timeMax=end + TIMEZONE_OFFSET,
            singleEvents=True,
            orderBy="startTime"
        ).execute()

        return result.get("items", [])

    except Exception as e:
        logger.error(
            "get_todays_events failed: %s", e
        )
        return []


def find_event_by_attendee(
    tenant_id: str,
    attendee_email: str,
    days_ahead: int = 30
) -> dict | None:
    """
    Find the most recently scheduled
    upcoming event for an attendee email.
    Returns event dict or None.
    """
    try:
        from services.google_auth import (
            get_calendar_service
        )
        import pytz
        from datetime import timezone as _tz
        
        service = get_calendar_service(
            tenant_id
        )
        if not service:
            return None
        
        IST = pytz.timezone("Asia/Kolkata")
        now = datetime.now(IST)
        future = now + timedelta(
            days=days_ahead
        )
        
        result = service.events().list(
            calendarId="primary",
            timeMin=now.isoformat(),
            timeMax=future.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            q=attendee_email
        ).execute()
        
        events = result.get("items", [])
        
        # Filter events that have this
        # attendee or were created by Frosty
        frosty_events = []
        for event in events:
            attendees = event.get(
                "attendees", []
            )
            attendee_emails = [
                a.get("email", "")
                for a in attendees
            ]
            desc = event.get(
                "description", ""
            ) or ""
            
            if (
                attendee_email in attendee_emails
                or "Frosty" in desc
                or "frosty" in desc.lower()
            ):
                frosty_events.append(event)
        
        if frosty_events:
            logger.info(
                "find_event_by_attendee "
                "found=%d for=%s",
                len(frosty_events),
                attendee_email
            )
            return frosty_events[0]
        
        # Return first upcoming event
        # if no Frosty-specific event found
        if events:
            return events[0]
        
        return None
    
    except Exception as e:
        logger.error(
            "find_event_by_attendee "
            "failed: %s", e
        )
        return None


def delete_event(
    tenant_id: str,
    event_id: str
) -> dict:
    """
    Delete a Google Calendar event by ID.
    Sends cancellation to all attendees.
    
    Returns:
        success: bool
        error: str
    """
    try:
        from services.google_auth import (
            get_calendar_service
        )
        service = get_calendar_service(
            tenant_id
        )
        if not service:
            return {
                "success": False,
                "error": "Calendar not connected"
            }
        
        service.events().delete(
            calendarId="primary",
            eventId=event_id,
            sendUpdates="all"
        ).execute()
        
        logger.info(
            "event_deleted event_id=%s "
            "tenant=%s",
            event_id, tenant_id
        )
        
        return {
            "success": True,
            "error": None
        }
    except Exception as e:
        logger.error(
            "delete_event failed: %s", e
        )
        return {
            "success": False,
            "error": str(e)
        }

def find_meeting_by_session(session_id: str) -> dict:
    """Find upcoming meeting for a session."""
    try:
        from services.google_auth import get_credentials
        from googleapiclient.discovery import build
        creds = get_credentials()
        if not creds:
            return {"success": False, "error": "Not authenticated"}
        service = build('calendar', 'v3', credentials=creds)
        now = datetime.utcnow().isoformat() + 'Z'
        events = service.events().list(
            calendarId='primary',
            timeMin=now,
            maxResults=50,
            singleEvents=True,
            orderBy='startTime',
            privateExtendedProperty=f'session_id={session_id}'
        ).execute()
        items = events.get('items', [])
        if not items:
            return {"success": False, "error": "No upcoming meeting found"}
        event = items[0]
        return {
            "success": True,
            "event_id": event['id'],
            "title": event.get('summary', ''),
            "start": event['start'].get('dateTime', ''),
            "attendees": [
                a['email']
                for a in event.get('attendees', [])
            ]
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

def cancel_meeting(event_id: str) -> dict:
    """Cancel a meeting by event ID."""
    try:
        from services.google_auth import get_credentials
        from googleapiclient.discovery import build
        creds = get_credentials()
        if not creds:
            return {"success": False, "error": "Not authenticated"}
        service = build('calendar', 'v3', credentials=creds)
        service.events().delete(
            calendarId='primary',
            eventId=event_id,
            sendUpdates='all'
        ).execute()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
