"""Rate limit keying for the public web chat views.

Paths carrying a session bucket per conversation, so one visitor cannot consume
the allowance of everyone else chatting to the same chatbot. The public entry
points run before a session exists and bucket on the caller's address instead.
"""

from apps.utils.rate_limit import client_ip, html_limited_response, rate_limited

PUBLIC_CHAT_SCOPE = "public_chat"


def public_chat_key(request, *args, **kwargs):
    session_id = kwargs.get("session_id")
    if session_id is None:
        return "ip", client_ip(request)
    return "session", str(session_id)


def public_chat_rate_limited(view_func):
    """Applies the public chat scope, its keying and its browser-facing 429 page."""
    return rate_limited(PUBLIC_CHAT_SCOPE, key_fn=public_chat_key, response_fn=html_limited_response)(view_func)
