from datetime import UTC, datetime

# Sunset details for the legacy embedded web chat flow (the `/embed/start/` endpoints
# and the pages they serve). Replaced by the chat widget backed by `/api/chat/*`.
# Tracking issue: https://github.com/dimagi/open-chat-studio/issues/3540
EMBED_FLOW_SUNSET_AT = datetime(2026, 8, 3, tzinfo=UTC)
EMBED_FLOW_SUCCESSOR_URL = "https://docs.openchatstudio.com/chat_widget/"

DEFAULT_CONSENT_TEXT = """
Welcome to this chatbot built on Open Chat Studio!

The Chatbot is provided "as-is" and "as available." Open Chat Studio makes no warranties,
express or implied, regarding the Chatbot's accuracy, completeness, or availability.

You use the chatbot at your own risk. Open Chat Studio shall not be liable for any harm
or damages that may result from your use of the chatbot.

You understand and agree that any reliance on the Chatbot's responses is solely at your own
discretion and risk.

By selecting “I Agree” below, you indicate that:

* You have read and understood the above information.
* You voluntarily agree to try out this chatbot.
* You are 18 years or older.
"""
