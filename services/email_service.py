from tools.email_tool import send_email

def send_cancellation_email(
    to_email: str,
    participant_name: str,
    meeting_title: str,
    meeting_time: str,
    admin_email: str
) -> bool:
    """Send cancellation email to user and admin."""
    try:
        user_body = f"""
Dear {participant_name},

Your meeting has been successfully cancelled.

Meeting: {meeting_title}
Scheduled Time: {meeting_time}

If you would like to reschedule, simply start a
new conversation and we will find a suitable time.

We look forward to connecting with you soon.

Best regards,
Frosty
        """.strip()

        admin_body = f"""
Meeting Cancelled

A meeting has been cancelled by a user.

Participant: {participant_name}
Email: {to_email}
Meeting: {meeting_title}
Scheduled Time: {meeting_time}

No action required unless you wish to follow up.
        """.strip()

        send_email(to_email, f"Meeting Cancelled: {meeting_title}", user_body)
        send_email(admin_email, f"[Admin] Meeting Cancelled: {meeting_title}", admin_body)
        return True
    except Exception as e:
        print(f"[cancellation_email] Failed: {e}")
        return False

def send_meeting_confirmation(
    to_email: str,
    participant_name: str,
    meeting_title: str,
    meeting_time: str,
    meet_link: str = "",
    event_id: str = ""
) -> bool:
    """Send meeting confirmation with a cancellation link."""
    from config import settings
    from tools.email_tool import send_email

    email_subject = "Your Meeting with Frostrek LLP is Confirmed"
    email_body = (
        f"Dear {participant_name},\n\n"
        f"Your meeting with Frostrek LLP has been "
        f"scheduled for {meeting_time}.\n\n"
    )
    if meet_link:
        email_body += (
            f"Google Meet Link: {meet_link}\n\n"
        )
    
    email_body += (
        f"Need to cancel? Click here:\n"
        f"{settings.BASE_URL}/cancel-meeting?event_id={event_id}&email={to_email}&name={participant_name}&title={meeting_title}\n\n"
        f"Please add this to your calendar.\n\n"
        f"Looking forward to speaking with you!\n\n"
        f"Best regards,\n"
        f"Frosty | AI Assistant\n"
        f"Frostrek LLP"
    )
    
    try:
        send_email(to_email, email_subject, email_body)
        return True
    except Exception as e:
        print(f"[meeting_confirmation] Failed: {e}")
        return False
