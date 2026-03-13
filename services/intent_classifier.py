"""
Intent Classifier — lightweight pre-classification
for incoming user messages before the full LangGraph agent.

Returns a coarse intent label for routing / context enrichment.
"""

from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from logger_config import logger

INTENT_LABELS = [
    "sales_inquiry",
    "support_request",
    "general_question",
    "contact_request",
    "feedback",
    "schedule_meeting",
    "send_email",
    "off_topic",
]

_CLASSIFIER_PROMPT = f"""You are Frosty, the official AI assistant of Frostrek LLP.
Your goal is to help users understand Frostrek solutions.

Classify the user message into EXACTLY ONE of these intents:
{', '.join(INTENT_LABELS)}

Rules:
- sales_inquiry: asking about services, pricing, projects, capabilities, or wanting to build something
- support_request: existing client needing help, bug reports, issues
- general_question: asking about the company, team, history, culture, or about Frosty
- contact_request: wants to talk to someone, share contact info, call
- feedback: giving feedback, reviews, suggestions
- schedule_meeting: wants to book, schedule, or arrange a meeting
- send_email: wants information sent to their email
- off_topic: completely unrelated to Frostrek or business

Respond with ONLY the intent label, nothing else."""


def classify_intent(user_text: str) -> str:
    """
    Classify user text into a coarse intent.

    Returns one of the INTENT_LABELS strings.
    Falls back to 'general_question' on errors.
    """
    if not user_text or not user_text.strip():
        return "general_question"

    try:
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0,
            max_tokens=20,
        )

        response = llm.invoke([
            SystemMessage(content=_CLASSIFIER_PROMPT),
            HumanMessage(content=user_text),
        ])

        intent = response.content.strip().lower().replace(" ", "_")

        # Validate against known labels
        if intent in INTENT_LABELS:
            logger.info(
                "intent_classifier: '%s' → %s",
                user_text[:60], intent,
            )
            return intent

        logger.warning(
            "intent_classifier: unexpected label '%s', "
            "falling back to general_question",
            intent,
        )
        return "general_question"

    except Exception as exc:
        logger.error("intent_classifier: failed: %s", exc)
        return "general_question"
