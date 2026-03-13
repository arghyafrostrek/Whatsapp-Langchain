from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict


# ---- Basic enums ----

InputType = Literal["text", "voice", "image", "email"]
IntentType = Literal[
    "schedule_meeting",
    "send_email",
    "send_email_only",
    "missing_information",
    "normal_chat",
    "confirmation_reply",
    "other",
]


# ---- Session & user tracking ----


class SessionInfo(TypedDict, total=False):
    session_id: str
    user_id: Optional[str]
    user_email: Optional[str]
    display_name: Optional[str]
    ticket_id: Optional[str]
    ticket_number: Optional[str]
    timestamp: Optional[str]


class InputMessage(TypedDict, total=False):
    input_type: InputType
    raw_message: Optional[str]
    normalized_text: Optional[str]
    audio_url: Optional[str]
    image_url: Optional[str]
    metadata: Dict[str, Any]
    is_voice: bool


# ---- Intent detection & structured payload ----


class MeetingData(TypedDict, total=False):
    meeting_date: Optional[str]
    meeting_time: Optional[str]
    meeting_title: Optional[str]
    participant_name: Optional[str]
    requested_datetime: Optional[str]
    end_datetime: Optional[str]
    search_start: Optional[str]
    search_end: Optional[str]
    auto_schedule: Optional[bool]
    auto_scheduled: Optional[bool]


class EmailData(TypedDict, total=False):
    email_address: Optional[str]
    email: Optional[str]
    email_subject: Optional[str]
    email_body: Optional[str]
    email_schema: Optional[Dict[str, Any]]


class IntentPayload(TypedDict, total=False):
    intent: Optional[IntentType]
    reply: Optional[str]
    chat_reply: Optional[str]
    meeting: MeetingData
    email: EmailData
    extra: Dict[str, Any]


# ---- 3-stage email flow ----


class EmailDraftState(TypedDict, total=False):
    has_draft: bool
    from_intent: bool
    email: EmailData
    draft_created_at: Optional[str]
    source_message_id: Optional[str]


class PendingEmailConfirmationState(TypedDict, total=False):
    awaiting_confirmation: bool
    pending_email: Optional[str]
    pending_subject: Optional[str]
    pending_body: Optional[str]
    target_email_address: Optional[str]
    session_id: Optional[str]
    updated_at: Optional[str]
    is_valid_confirmation: Optional[bool]
    confirmation_message: Optional[str]


class SentEmailState(TypedDict, total=False):
    sent: bool
    sent_at: Optional[str]
    provider_message_id: Optional[str]
    error: Optional[str]


class EmailFlowState(TypedDict, total=False):
    draft: EmailDraftState
    pending_confirmation: PendingEmailConfirmationState
    sent: SentEmailState


# ---- Meeting scheduling & calendar ----


class CalendarAvailability(TypedDict, total=False):
    is_available: Optional[bool]
    conflicting_events: List[Dict[str, Any]]
    checked_range_start: Optional[str]
    checked_range_end: Optional[str]


class CalendarEventData(TypedDict, total=False):
    calendar_event_id: Optional[str]
    start_datetime: Optional[str]
    end_datetime: Optional[str]
    summary: Optional[str]
    attendee_email: Optional[str]
    participant: Optional[str]
    auto_scheduled: Optional[bool]


class MeetingFlowState(TypedDict, total=False):
    validated: bool
    validation_errors: List[str]
    missing_fields: List[str]
    auto_schedule_branch: Optional[bool]
    availability: CalendarAvailability
    candidate_event: CalendarEventData
    created_event: CalendarEventData


# ---- Voice input & audio response ----


class VoiceState(TypedDict, total=False):
    is_voice_input: bool
    needs_voice_response: bool
    audio_mime_type: Optional[str]
    tts_audio_id: Optional[str]
    stt_model_used: Optional[str]
    tts_model_used: Optional[str]


# ---- Knowledge base / context ----


class KnowledgeBaseContext(TypedDict, total=False):
    services: Optional[str]
    about: Optional[str]
    benefits: Optional[str]
    contact: Optional[str]
    greeting: Optional[str]
    campus_academic_collaborations: Optional[str]
    team_leadership: Optional[str]
    faq: Optional[str]
    talent_hunt: Optional[str]
    navigations: Optional[str]
    industry_partners: Optional[str]
    client_team_feedback: Optional[str]
    reference_estimation: Optional[str]
    voice_tone: Optional[str]
    raw_sheet_data: List[Dict[str, Any]]


# ---- Structured JSON responses / model I/O ----


class ModelIOState(TypedDict, total=False):
    last_model_raw: Optional[Any]
    all_model_raw: List[Any]
    structured: IntentPayload
    api_response: Dict[str, Any]
    error: Optional[str]
    debug: Dict[str, Any]


# ---- Top-level LangGraph state ----


class State(TypedDict, total=False):
    session: SessionInfo
    input: InputMessage
    intent: IntentPayload
    email_flow: EmailFlowState
    meeting_flow: MeetingFlowState
    voice: VoiceState
    knowledge_base: KnowledgeBaseContext
    model_io: ModelIOState
    memory_keys: Dict[str, Any]
    scratch: Dict[str, Any]
    awaiting_confirmation: Optional[bool]
    recipient: Optional[str]
    subject: Optional[str]
    body: Optional[str]

